from app.schemas.core import ReviewMutation, ClauseResult
from app.schemas.model_outputs import SemanticOutput
from app.services.tasks import TaskService
from app.services.memory import ProjectMemoryService
from app.services.knowledge import KnowledgeService
from app.domain.errors import DomainError
from app.domain.export_gate import result_blockers, validation_digest
from app.domain.output_checks import validate_response
from app.workflows.prompts import render_prompt
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.contracts import ModelRequest
from app.domain.source_context import model_source_context


class ReviewService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self.knowledge = KnowledgeService.from_settings(repo, settings)

    def binding(self, run_id, clause_id):
        matches = [b for b in self.repo.list('run_binding', parent_id=run_id, limit=100000) if b['clause_id'] == clause_id]
        if len(matches) != 1:
            raise DomainError('CLAUSE_NOT_FOUND', '条款不属于该运行', 404)
        return matches[0]

    def states(self, result):
        ids = sorted({r['source_version_id'] for a in result['assessments'] for r in a['evidence_refs']})
        return [{'id': i, 'version': self.repo.get('source_version', i)['version'], 'status': self.repo.get('source_version', i)['status']} for i in ids]

    def apply_review(self, run_id, clause_id, body):
        mutation = ReviewMutation.model_validate({**body, 'run_id': run_id, 'clause_id': clause_id})
        with self.repo.transaction():
            run = TaskService(self.repo, self.settings).assert_active(run_id)
            binding = self.binding(run_id, clause_id)
            if binding['version'] != mutation.expected_version:
                raise DomainError('VERSION_CONFLICT', '条款审核版本已变化，请刷新', 409)
            if not binding.get('result_id'):
                raise DomainError('RESULT_NOT_READY', '条款尚未完成', 409)
            record = self.repo.get('clause_result', binding['result_id'], run['project_id'])
            result = ClauseResult.model_validate(record['result'])
            changes = {}
            if mutation.action == 'comment':
                if not mutation.comments or not mutation.comments.strip():
                    raise DomainError('COMMENT_REQUIRED', '请输入批注', 422)
                self.repo.put('review_comment', {'run_id': run_id, 'clause_id': clause_id, 'result_id': record['id'], 'text': mutation.comments}, run['project_id'], binding['id'])
            elif mutation.action == 'approve':
                products = self.repo.get('config_snapshot', run['config_snapshot_id'])['products']
                blockers = result_blockers(result, run['product_id'], products, self.knowledge)
                if blockers:
                    raise DomainError('REVIEW_BLOCKED', '条款未通过证据或输出校验，不能批准', 409, {'blockers': blockers})
                digest = validation_digest(record['result'], self.states(record['result']))
                review = self.repo.put('review', {'run_id': run_id, 'clause_id': clause_id, 'result_id': record['id'], 'result_version': record['version'], 'validation_hash': digest, 'status': 'approved'}, run['project_id'], binding['id'])
                changes = {'review_status': 'approved', 'approved_review_id': review['id']}
                ProjectMemoryService(self.repo).remember(run['project_id'], record['id'], review['id'], {'product_id': run['product_id'], 'raw_text': result.raw_text,
                    'response_en': result.response_en, 'source_versions': self.states(record['result'])}, run['mode'])
            elif mutation.action == 'revoke':
                changes = {'review_status': 'revoked', 'approved_review_id': None}
            else:
                edits = mutation.edits or {}
                if set(edits) != {'response_en'} or not isinstance(edits['response_en'], str):
                    raise DomainError('FACT_EDIT_REQUIRES_RUN', '事实、型号、判定或证据变化请使用重新运行；审核编辑仅接受英文措辞', 422)
                if result.decision.value in {'UNKNOWN', 'NC'}:
                    raise DomainError('WORDING_EDIT_NOT_ALLOWED', 'UNKNOWN 无正式口径，NC 使用固定文案；请补证并重新运行', 409)
                edited = result.model_copy(update={'response_en': edits['response_en'], 'version': result.version + 1, 'output_valid': False})
                products = self.repo.get('config_snapshot', run['config_snapshot_id'])['products']
                if not validate_response(edited, run['product_id'], products):
                    raise DomainError('OUTPUT_INVALID', '编辑后的口径未通过数字、型号或引用检查', 422)
                new = self.repo.put('clause_result', {'result': edited.model_dump(mode='json'), 'run_id': run_id, 'clause_id': clause_id, 'parent_result_id': record['id']}, run['project_id'], run_id)
                changes = {'result_id': new['id'], 'review_status': 'pending', 'approved_review_id': None}
                self.repo.put('job', {'kind': 'review_validation', 'run_id': run_id, 'clause_id': clause_id, 'result_id': new['id'], 'status': 'queued', 'cancel_requested': False}, run['project_id'], run_id)
            updated = self.repo.update('run_binding', binding['id'], changes, mutation.expected_version)
            self.repo.audit(run['project_id'], 'review.' + mutation.action, {'run_id': run_id, 'clause_id': clause_id, 'result_id': updated['result_id']})
            return updated

    def validate_wording(self, job, transport=None):
        run = TaskService(self.repo, self.settings).assert_active(job['run_id'])
        record = self.repo.get('clause_result', job['result_id'], run['project_id'])
        config = self.repo.get('config_snapshot', run['config_snapshot_id'])
        result = record['result']
        binding = self.binding(run['id'], job['clause_id'])
        data = {'claim': {'text': result['raw_text']}, 'assessment': {'verdict': result['decision'], 'response_en': result['response_en']},
                'evidence': [r for a in result['assessments'] for r in a['evidence_refs']]}
        source_context = model_source_context(binding.get('source_context', {}))
        if source_context:
            data['source_context'] = source_context
        snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
        if snapshot.get('lifecycle_context', {}).get('reference_date'):
            data['lifecycle_context'] = snapshot['lifecycle_context']
        messages, _ = render_prompt('verify_semantic_support', data, SemanticOutput.model_json_schema(), config['prompts'])
        from app.security.execution_policy import policy_for
        gateway = ModelGateway(self.repo, config['models'], self.settings.secrets, transport,
            policy=lambda: policy_for(self.settings, frozen=config.get('execution_policy') or policy_for(self.settings)))
        # 每次人工编辑是独立结果版本，验证使用新 job 的持久预算，不重置业务运行预算。
        response = gateway.invoke(ModelRequest(job['id'], job['clause_id'], '__wording__', 'verify_semantic_support', messages, SemanticOutput, config['id']))
        if response.error_code:
            raise DomainError(response.error_code, '措辞语义核验未完成，暂不可批准', 503)
        valid = response.payload['support'] == 'SUPPORTED'
        with self.repo.transaction():
            binding = self.binding(run['id'], job['clause_id'])
            if binding['result_id'] != record['id']:
                raise DomainError('VERSION_CONFLICT', '已有更新的措辞版本，本次验证作废', 409)
            result = {**result, 'output_valid': valid}
            if not valid:
                result.update(decision='UNKNOWN', response_en=None, reason_codes=['OUTPUT_INVALID'])
            validated = self.repo.put('clause_result', {'result': result, 'run_id': run['id'], 'clause_id': job['clause_id'], 'parent_result_id': record['id']}, run['project_id'], run['id'])
            self.repo.update('run_binding', binding['id'], {'result_id': validated['id'], 'review_status': 'pending', 'approved_review_id': None})
