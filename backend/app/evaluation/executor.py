import time
import copy
from app.evaluation.provenance import provenance
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash
from app.schemas.core import RunContext
from app.services.tasks import TaskService
from app.services.knowledge import KnowledgeService
from app.workflows.soc_graph import SocWorkflow
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.usage import summarize_usage
from app.evaluation.datasets import execution_input
from app.evaluation.scoring import score
from app.evaluation.state import terminal_report


class EvaluationExecutor:
    def __init__(self, repo, settings, transport=None, retriever=None):
        self.repo, self.settings, self.transport, self.retriever = repo, settings, transport, retriever

    def enqueue(self, dataset_version_id, config_ids=None):
        version = self.repo.get('dataset_version', dataset_version_id)
        products = {c['product_id'] for c in version['cases']}
        if config_ids is not None:
            if set(config_ids) != products:
                raise ValueError('Frozen configuration must cover dataset products')
            for product, ident in config_ids.items():
                config = self.repo.get('config_snapshot', ident)
                if config.get('knowledge_snapshot_id'):
                    snapshot = self.repo.get('knowledge_snapshot', config['knowledge_snapshot_id'])
                    if snapshot['product_id'] != product:
                        raise ValueError('Frozen knowledge product mismatch')
        with self.repo.transaction():
            projects = [p for p in self.repo.list('project') if p.get('evaluation_only')]
            project = projects[0] if projects else self.repo.put('project', {'name': '评测隔离区', 'default_product_id': 'M6000-SE', 'evaluation_only': True})
            configs = dict(config_ids) if config_ids is not None else {product: TaskService(self.repo, self.settings).config_snapshot(product)['id'] for product in products}
            run = self.repo.put('evaluation_run', {'dataset_version_id': dataset_version_id, 'configs': configs, 'status': 'queued', 'results': [], 'provenance': provenance(self.settings.root)}, project['id'])
            job = self.repo.put('job', {'kind': 'evaluation', 'evaluation_run_id': run['id'], 'status': 'queued', 'cancel_requested': False, 'processed_items': 0, 'total_items': len(version['cases'])}, project['id'], run['id'])
        from app.telemetry.experiments import prepare_experiment
        prepare_experiment(self.repo, self.settings, run['id'])
        return {'evaluation_run_id': run['id'], 'job_id': job['id'], 'status': 'queued'}

    def execute(self, job):
        from app.worker.runner import LazyEmbedding
        from app.rag.library_retrieval import LibraryRetriever
        evaluation = self.repo.get('evaluation_run', job['evaluation_run_id'])
        dataset = self.repo.get('dataset_version', evaluation['dataset_version_id'])
        knowledge = KnowledgeService.from_settings(self.repo, self.settings)
        results = list(evaluation.get('results', []))
        started = time.monotonic()
        self.repo.update('evaluation_run', evaluation['id'], {'status': 'running', 'execution_provenance': provenance(self.settings.root)})
        for index, case in enumerate(dataset['cases']):
            case_id = case.get('case_id') or 'legacy-' + str(index)
            previous = next((r for r in results if r.get('case_id') == case_id), None)
            if previous and previous.get('status') in {'completed', 'failed'}:
                continue
            if self.repo.get('job', job['id']).get('cancel_requested'):
                self.finalize(evaluation['id'], results, 'cancelled', started)
                raise DomainError('JOB_CANCELLED', '评测已取消', 409)
            safe_input = execution_input(case)
            config = self.repo.get('config_snapshot', evaluation['configs'][safe_input['product_id']])
            existing = [r for r in self.repo.list('evaluation_case_run', parent_id=evaluation['id'], limit=100000) if r.get('case_id', 'legacy-' + str(r.get('case_index'))) == case_id]
            execution = existing[0] if existing else self.repo.put('evaluation_case_run', {'case_id': case_id, 'case_index': index}, evaluation['project_id'], evaluation['id'])
            context = RunContext(run_id=execution['id'], mode='EVALUATION', local_user_id=self.repo.local_user_id, project_id=evaluation['project_id'],
                product_id=safe_input['product_id'], config_snapshot_id=config['id'], knowledge_snapshot_id=config['knowledge_snapshot_id'],
                source_context=safe_input.get('source_context', {}), lifecycle_context=safe_input.get('lifecycle_context', {}),
                evaluation_run_id=evaluation['id'], job_id=job['id'])
            from app.security.execution_policy import policy_for
            gateway = ModelGateway(self.repo, config['models'], self.settings.secrets, self.transport,
                policy=lambda: policy_for(self.settings, frozen=config.get('execution_policy') or policy_for(self.settings)))
            frozen = copy.copy(self.settings)
            frozen.local = {**self.settings.local, **config['retrieval']}
            frozen.products, frozen.processing = config['products'], config.get('processing', {})
            frozen.response_product_name = config.get('response_product_name',safe_input['product_id'])
            frozen.execution_policy = config.get('execution_policy')
            retrieval = self.retriever or LibraryRetriever(self.repo, knowledge, LazyEmbedding(frozen), frozen, config['retrieval']['retrieval_top_k'], config['retrieval']['rrf_k'])
            case_started = time.monotonic()
            try:
                result = SocWorkflow(self.repo, frozen, gateway, retrieval, knowledge, config['prompts']).run_clause(execution['id'], safe_input['raw_text'], context).model_dump(mode='json')
                result['status'] = 'failed' if result.get('diagnostics', {}).get('failure') else 'completed'
            except Exception as exc:
                if isinstance(exc, DomainError) and exc.code == 'JOB_CANCELLED':
                    self.finalize(evaluation['id'], results, 'cancelled', started)
                    raise
                if isinstance(exc, DomainError) and exc.code == 'EXECUTION_OFFLINE':
                    self.finalize(evaluation['id'], results, 'interrupted', started)
                    raise
                result = {'status': 'failed', 'error_code': getattr(exc, 'code', type(exc).__name__)}
            result.update(case_id=case_id, execution_id=execution['id'], elapsed_seconds=time.monotonic() - case_started)
            results = [r for r in results if r.get('case_id') != case_id] + [result]
            self.repo.update('evaluation_case_run', execution['id'], {'status': result['status'], 'result': result})
            self.repo.update('evaluation_run', evaluation['id'], {'results': results})
            self.repo.update('job', job['id'], {'processed_items': len(results), 'stage': 'assess'})
        status = 'failed' if any(r['status'] == 'failed' for r in results) else 'completed'
        self.finalize(evaluation['id'], results, status, started)

    def finalize(self, ident, results, status, started=None):
        evaluation = self.repo.get('evaluation_run', ident)
        dataset = self.repo.get('dataset_version', evaluation['dataset_version_id'])
        attempts = [a for r in self.repo.list('evaluation_case_run', parent_id=ident, limit=100000)
                    for a in ModelGateway(self.repo, {}, {}).attempts(run_id=r['id'])]
        report = {**terminal_report(dataset['cases'], results, status), 'elapsed_seconds': sum(r.get('elapsed_seconds', 0) for r in results),
            'usage': summarize_usage(attempts), 'configs': evaluation['configs'], 'dataset_version_id': dataset['id']}
        self.repo.update('evaluation_run', ident, {'status': status, 'results': results, 'report': report})
        from app.telemetry.experiments import mirror_experiment
        mirror_experiment(self.repo, self.settings, ident)
        return report

    def rescore(self, ident):
        evaluation = self.repo.get('evaluation_run', ident)
        dataset = self.repo.get('dataset_version', evaluation['dataset_version_id'])
        report = score(dataset['cases'], evaluation.get('results', []))
        item = self.repo.put('evaluation_score', {'report': report, 'source_results_hash': content_hash(evaluation.get('results', []))}, evaluation['project_id'], ident)
        from app.telemetry.experiments import mirror_scores
        mirror_scores(self.repo, self.settings, evaluation, item)
        return item
