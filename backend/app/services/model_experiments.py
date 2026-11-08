"""Bounded split probes with frozen inputs, isolated records and no business writes."""
import json
from pathlib import Path
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, canonical, file_hash
from app.domain.split_validation import resolve_claims
from app.model_gateway.contracts import ModelRequest, ProviderFailure
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.adapters import ChatCompletionsAdapter
from app.model_gateway.usage import summarize_usage
from app.persistence.repositories import Repository
from app.schemas.model_outputs import SplitOutput
from app.services.run_reports import RunReportService
from app.services.tasks import TaskService
from app.workflows.prompts import render_prompt


class ModelExperimentService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    @staticmethod
    def validator_hash():
        domain = Path(__file__).resolve().parents[1] / 'domain'
        return content_hash({name: file_hash(domain / name) for name in ['split_validation.py', 'requirement_relations.py', 'output_checks.py']})

    def prepare(self, baseline_run_id, clause_ids):
        if not 1 <= len(clause_ids) <= 6 or len(set(clause_ids)) != len(clause_ids):
            raise DomainError('EXPERIMENT_LIMIT', '一次固定样本试验仅接受 1 至 6 条不重复样本', 422)
        run = self.repo.get('run', baseline_run_id)
        service = RunReportService(self.repo, self.settings)
        rows = {r['clause_id']: r for r in service.clauses(baseline_run_id)}
        if not set(clause_ids).issubset(rows):
            raise DomainError('CLAUSE_NOT_FOUND', '样本必须来自指定基线运行', 422)
        attempts = service.attempts(baseline_run_id)
        config = TaskService(self.repo, self.settings).config_snapshot(run['product_id'])
        original_config = self.repo.get('config_snapshot', run['config_snapshot_id'])
        cases = []
        for ident in clause_ids:
            row = rows[ident]
            if not row['machine_result']:
                raise DomainError('EXPERIMENT_RESULT_REQUIRED', '样本尚无原始机器结果', 422)
            cases.append({'clause_id': ident, 'coordinate': row.get('coordinate'), 'raw_text': row['raw_text'],
                          'translation_zh': row['machine_result'].get('translation_zh', ''),
                          'baseline': {'coverage_complete': row['machine_result']['coverage_complete'],
                              'reason_codes': row['machine_result']['reason_codes'],
                              'usage': summarize_usage([a for a in attempts if a['clause_id'] == ident and a['logical_node_name'] == 'split_claims'])}})
        spec = {'version': 1, 'phase': 'split_only', 'baseline_run_id': baseline_run_id, 'cases': cases,
                'config': config, 'baseline_config_hash': original_config['content_hash'],
                'prompt_hash': content_hash(config['prompts']), 'model_hash': content_hash(config['models']),
                'knowledge_snapshot_id': config['knowledge_snapshot_id'], 'schema_hash': content_hash(SplitOutput.model_json_schema()),
                'validator_hash': self.validator_hash(),
                'max_calls': len(cases), 'attempts_per_case': 1, 'automatic_retry': False, 'include_adjacent_context': False,
                'business_writes': False, 'quality_scope': '结构与字面覆盖；引用正确率和误判率需独立端到端验收'}
        return self.repo.put('model_experiment', {'spec': spec, 'manifest_hash': content_hash(spec),
            'max_calls': len(cases), 'status': 'prepared'}, run['project_id'], baseline_run_id)

    def execute(self, manifest_id, expected_hash, transport=None, secrets=None):
        manifest = self.repo.get('model_experiment', manifest_id)
        spec = manifest['spec']
        if manifest['manifest_hash'] != expected_hash or content_hash(spec) != expected_hash:
            raise DomainError('EXPERIMENT_CHANGED', '样本或配置已变化，必须重新核对清单', 409)
        if content_hash(SplitOutput.model_json_schema()) != spec['schema_hash']:
            raise DomainError('EXPERIMENT_SCHEMA_CHANGED', '拆分结构已变化，请准备新的清单', 409)
        folder = self.repo.data_dir / 'experiments' / manifest_id
        folder.mkdir(parents=True, exist_ok=True)
        completed = folder / 'report.json'
        if completed.exists():
            return json.loads(completed.read_text('utf-8'))
        if spec.get('validator_hash') != self.validator_hash():
            raise DomainError('EXPERIMENT_VALIDATOR_CHANGED', '校验代码已变化，请准备新的清单', 409)
        lock = folder / 'execution.lock'
        try:
            guard = lock.open('x')
        except FileExistsError as exc:
            raise DomainError('EXPERIMENT_BUSY', '试验正在执行或上次进程中断；先核对已有调用记录，不能重复发送', 409) from exc
        try:
            isolated = Repository(folder)
            from app.security.execution_policy import policy_for
            live_policy = lambda: policy_for(self.settings, frozen=spec['config'].get('execution_policy') or policy_for(self.settings))
            actual = transport or ChatCompletionsAdapter(self.settings.secrets, policy=live_policy, data_dir=self.settings.data_dir)
            # A timeout consumes this case's one attempt; no hidden retries/failover in probes.
            def once(provider, messages, schema):
                try:
                    return actual(provider, messages, schema)
                except ProviderFailure as exc:
                    raise ProviderFailure(exc.code, False) from exc
            gateway = ModelGateway(isolated, spec['config']['models'], secrets if secrets is not None else self.settings.secrets, once,
                policy=live_policy)
            cases = []
            for case in spec['cases']:
                from app.workflows.soc_graph import SOC_WORKFLOW_VERSION
                data = {'raw_text': case['raw_text']}
                if spec['config'].get('workflow_version') != SOC_WORKFLOW_VERSION:
                    data['translation_zh'] = case['translation_zh']
                messages, _ = render_prompt('split_claims', data, SplitOutput.model_json_schema(), spec['config']['prompts'])
                request = ModelRequest(manifest_id, case['clause_id'], '__clause__', 'split_claims', messages, SplitOutput, spec['config']['id'])
                old = gateway.attempts(request)
                if old:
                    # Never resend after an uncertain response/commit boundary.
                    cases.append({'clause_id': case['clause_id'], 'coordinate': case['coordinate'], 'error_code': 'PREVIOUS_ATTEMPT_EXISTS',
                                  'coverage_complete': False, 'usage': summarize_usage(old), 'baseline': case['baseline']})
                    continue
                result = gateway.invoke(request)
                diagnostics = {'schema_valid': False, 'coverage_complete': False}
                if result.payload:
                    _, diagnostics = resolve_claims(case['raw_text'], result.payload['claims'], case['clause_id'])
                calls = gateway.attempts(request)
                cases.append({'clause_id': case['clause_id'], 'coordinate': case['coordinate'], 'error_code': result.error_code,
                    'coverage_complete': diagnostics['coverage_complete'], 'diagnostics': diagnostics,
                    'output_characters': calls[-1]['diagnostics']['output_characters'] if calls else None,
                    'usage': summarize_usage(calls), 'baseline': case['baseline'], 'payload': result.payload})
            report = {'manifest_id': manifest_id, 'manifest_hash': expected_hash, 'phase': spec['phase'],
                'cases': cases, 'coverage_passed': sum(c['coverage_complete'] for c in cases), 'total': len(cases),
                'schema_passed': sum(c.get('diagnostics', {}).get('schema_valid', False) for c in cases),
                'usage': summarize_usage(gateway.attempts(run_id=manifest_id)),
                'citation_accuracy': None, 'false_positive_rate': None, 'formal_response_length': None,
                'limitations': spec['quality_scope']}
            completed.write_text(canonical(report), 'utf-8')
            self.repo.update('model_experiment', manifest_id, {'status': 'completed', 'report_path': str(completed.relative_to(self.repo.data_dir))})
            return report
        finally:
            guard.close()
            lock.unlink()
