"""Reviewable input revisions. Preparing a plan never queues a model job."""
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, file_hash
from app.services.files import FileService
from app.services.run_reports import RunReportService
from app.services.tasks import TaskService
from app.schemas.core import LifecycleContext


class ScopePlanService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def _rows(self, run_id):
        return RunReportService(self.repo, self.settings).clauses(run_id)

    @staticmethod
    def _annotation_digest(rows):
        return content_hash([{'clause_id': r['clause_id'], 'result_id': r.get('result_id'),
                              'annotation': r['annotation']} for r in rows])

    def prepare(self, run_id, excluded_clause_ids=None, lifecycle_context=None):
        with self.repo.transaction():
            run = TaskService(self.repo, self.settings).assert_active(run_id)
            if run['mode'] != 'SOC_BATCH':
                raise DomainError('SCOPE_BATCH_REQUIRED', '范围方案仅用于 Excel 批任务', 422)
            snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
            rows = self._rows(run_id)
            eligible = {r['clause_id']: r for r in rows if r['annotation'] and not r['annotation_stale']
                        and r['annotation']['verdict'] == 'not_requirement'}
            selected = set(eligible if excluded_clause_ids is None else excluded_clause_ids)
            if not selected.issubset(eligible):
                raise DomainError('SCOPE_ANNOTATION_REQUIRED', '排除项必须有绑定当前结果的“不需应答”标注', 422)
            if len(selected) >= len(snapshot['clauses']):
                raise DomainError('SCOPE_EMPTY', '范围方案至少保留一条应答项', 422)
            path = FileService(self.repo).resolve(snapshot['file_id'], run['project_id'])
            if file_hash(path) != snapshot['file_sha256']:
                raise DomainError('INPUT_CHANGED', '原工作簿校验失败', 409)
            lifecycle = LifecycleContext.model_validate(snapshot.get('lifecycle_context', {}) if lifecycle_context is None else lifecycle_context).model_dump(mode='json')
            excluded = [{'clause_id': r['clause_id'], 'raw_text': r['raw_text'], 'coordinate': r['coordinate'],
                         'annotation_id': r['annotation']['id'], 'notes': r['annotation']['notes']}
                        for r in rows if r['clause_id'] in selected]
            all_excluded = {e['clause_id']: e for e in snapshot.get('excluded', [])}
            all_excluded.update({e['clause_id']: e for e in excluded})
            mapping = self.repo.get('mapping', snapshot['mapping_id'], run['project_id'])
            from app.excel.requirements import build_requirement_clauses
            clauses = build_requirement_clauses(path,
                [c for c in snapshot['clauses'] if c['id'] not in selected], mapping['selection'],
                excluded=list(all_excluded.values()), expected_sha=snapshot['file_sha256'])
            payload = {k: snapshot.get(k) for k in ['raw_text', 'mapping_id', 'file_id', 'file_sha256', 'header_targets']}
            payload.update(clauses=clauses, parent_input_snapshot_id=snapshot['id'], excluded=list(all_excluded.values()), lifecycle_context=lifecycle)
            revised = self.repo.put('input_snapshot', {**payload, 'content_hash': content_hash(payload)}, run['project_id'])
            return self.repo.put('scope_plan', {'base_run_id': run_id, 'task_id': run['task_id'], 'status': 'draft',
                'input_snapshot_id': revised['id'], 'original_count': len(rows), 'included_count': len(clauses),
                'excluded_count': len(excluded), 'excluded': excluded, 'lifecycle_context': lifecycle,
                'prior_excluded_count': len(snapshot.get('excluded', [])),
                'annotation_digest': self._annotation_digest(rows), 'input_hash': revised['content_hash']}, run['project_id'], run_id)

    def start(self, plan_id, expected_task_version):
        with self.repo.transaction():
            plan = self.repo.get('scope_plan', plan_id)
            task = self.repo.get('task', plan['task_id'], plan['project_id'])
            if plan['status'] != 'draft' or task['version'] != expected_task_version or task['active_run_id'] != plan['base_run_id']:
                raise DomainError('VERSION_CONFLICT', '范围方案或当前任务已变化，请刷新', 409)
            if plan['annotation_digest'] != self._annotation_digest(self._rows(plan['base_run_id'])):
                raise DomainError('SCOPE_STALE', '人工标注或结果已变化，请重新生成范围方案', 409)
            if any(j['status'] in {'queued', 'running', 'interrupted'} for j in self.repo.list('job', parent_id=plan['base_run_id'])):
                raise DomainError('JOB_ACTIVE', '当前运行仍有未结束的作业，请先完成或取消', 409)
            snapshot = self.repo.get('input_snapshot', plan['input_snapshot_id'], task['project_id'])
            path = FileService(self.repo).resolve(snapshot['file_id'], task['project_id'])
            if file_hash(path) != snapshot['file_sha256']:
                raise DomainError('INPUT_CHANGED', '原工作簿校验失败', 409)
            tasks = TaskService(self.repo, self.settings)
            snapshot = tasks.refresh_batch_snapshot(snapshot)
            accepted = tasks._create_run(task, snapshot, tasks.config_snapshot(task['product_id']), task['product_id'], plan['base_run_id'], {})
            self.repo.update('scope_plan', plan_id, {'status': 'started', 'run_id': accepted['run_id'], 'effective_input_snapshot_id': snapshot['id']})
            return accepted
