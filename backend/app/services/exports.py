from collections import Counter
from pathlib import Path
from app.schemas.core import ClauseResult, ExportReadiness
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, file_hash
from app.domain.export_gate import result_blockers, validation_digest
from app.services.tasks import TaskService
from app.services.reviews import ReviewService
from app.services.files import FileService
from app.services.knowledge import KnowledgeService
from app.excel.package_patch import patch_workbook
from app.excel.fidelity import verify_fidelity


class ExportService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self.knowledge = KnowledgeService.from_settings(repo, settings)
        self.reviews = ReviewService(repo, settings)

    def get_export_readiness(self, run_id):
        with self.repo.transaction():
            run = self.repo.get('run', run_id)
            products = self.repo.get('config_snapshot', run['config_snapshot_id'])['products']
            task = self.repo.get('task', run['task_id'], run['project_id'])
            snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
            bindings = self.repo.list('run_binding', parent_id=run_id, limit=100000)
            blockers, manifest = [], []
            def block(code, clause_id=None):
                blockers.append({'code': code, 'clause_id': clause_id})
            if task['active_run_id'] != run_id:
                block('RUN_SUPERSEDED')
            if run['mode'] == 'PRODUCT_QA':
                block('QA_NOT_SOC')
            if not snapshot.get('mapping_id'):
                block('NO_EXCEL_MAPPING')
            else:
                mapping = self.repo.get('mapping', snapshot['mapping_id'], run['project_id'])
                try:
                    path = FileService(self.repo).resolve(snapshot['file_id'], run['project_id'])
                    if file_hash(path) != snapshot['file_sha256'] or mapping['file_sha256'] != snapshot['file_sha256']:
                        block('INPUT_CHANGED')
                except DomainError:
                    block('INPUT_CHANGED')
            expected = [c['id'] for c in snapshot['clauses']]
            if not expected or len(bindings) != len(expected) or {b['clause_id'] for b in bindings} != set(expected):
                block('MANIFEST_INCOMPLETE')
            for binding in bindings:
                cid = binding['clause_id']
                item = {'binding_id': binding['id'], 'version': binding['version'], 'clause_id': cid, 'result_id': binding.get('result_id'), 'review_id': binding.get('approved_review_id')}
                if not binding.get('result_id'):
                    block('RESULT_MISSING', cid)
                    manifest.append(item)
                    continue
                record = self.repo.get('clause_result', binding['result_id'], run['project_id'])
                result = ClauseResult.model_validate(record['result'])
                for code in result_blockers(result, run['product_id'], products, self.knowledge):
                    block(code, cid)
                if result.raw_text != binding['raw_text']:
                    block('INPUT_CHANGED', cid)
                states = self.reviews.states(record['result'])
                if run.get('knowledge_snapshot_id'):
                    source_ids = set(self.repo.get('knowledge_snapshot', run['knowledge_snapshot_id'])['source_version_ids'])
                    if any(s['id'] not in source_ids for s in states):
                        block('EVIDENCE_NOT_IN_SNAPSHOT', cid)
                elif states:
                    block('EVIDENCE_NOT_IN_SNAPSHOT', cid)
                item.update(result_version=record['version'], result_hash=content_hash(record['result']), sources=states)
                if binding['review_status'] != 'approved' or not binding.get('approved_review_id'):
                    block('NOT_REVIEWED', cid)
                else:
                    review = self.repo.get('review', binding['approved_review_id'], run['project_id'])
                    if review['result_id'] != record['id'] or review['result_version'] != record['version'] or review['validation_hash'] != validation_digest(record['result'], states):
                        block('REVIEW_INVALIDATED', cid)
                manifest.append(item)
            digest = content_hash({'run_id': run_id, 'active_run_id': task['active_run_id'], 'input_hash': snapshot['content_hash'], 'items': manifest})
            return ExportReadiness(eligible=not blockers, blocker_counts=dict(Counter(b['code'] for b in blockers)), blocker_items=blockers, snapshot_hash=digest)

    def create_export(self, run_id, expected_snapshot_hash):
        with self.repo.transaction():
            readiness = self.get_export_readiness(run_id)
            if not readiness.eligible or readiness.snapshot_hash != expected_snapshot_hash:
                raise DomainError('EXPORT_BLOCKED', '整批导出条件未满足或版本已变化', 409, {'blockers': readiness.blocker_items})
            run = self.repo.get('run', run_id)
            snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
            artifact = self.repo.put('export', {'run_id': run_id, 'snapshot_hash': readiness.snapshot_hash, 'input_snapshot_id': snapshot['id'],
                'mapping_id': snapshot['mapping_id'], 'status': 'queued', 'manifest': self.repo.list('run_binding', parent_id=run_id, limit=100000)}, run['project_id'], run_id)
            job = self.repo.put('job', {'kind': 'export', 'run_id': run_id, 'export_id': artifact['id'], 'status': 'queued', 'cancel_requested': False}, run['project_id'], run_id)
            return {'export_id': artifact['id'], 'job_id': job['id'], 'status': 'queued'}

    def _require_current(self, artifact):
        readiness = self.get_export_readiness(artifact['run_id'])
        if not readiness.eligible or readiness.snapshot_hash != artifact['snapshot_hash']:
            self.repo.update('export', artifact['id'], {'status': 'invalidated', 'invalid_reason': 'VERSION_OR_SOURCE_CHANGED'})
            raise DomainError('EXPORT_INVALIDATED', '来源、审核或运行已变化，导出文件已失效', 409, {'blockers': readiness.blocker_items})

    def execute(self, job):
        artifact = self.repo.get('export', job['export_id'], job['project_id'])
        self._require_current(artifact)
        snapshot = self.repo.get('input_snapshot', artifact['input_snapshot_id'], job['project_id'])
        source = FileService(self.repo).resolve(snapshot['file_id'], job['project_id'])
        values = {sheet: dict(cells) for sheet, cells in (snapshot.get('header_targets') or {}).items()}
        for binding in artifact['manifest']:
            result = self.repo.get('clause_result', binding['result_id'], job['project_id'])['result']
            coordinate = binding['coordinate']
            cells = values.setdefault(coordinate['sheet'], {})
            citations = []
            for assessment in result['assessments']:
                for ref in assessment['evidence_refs']:
                    locator = ', '.join(f'{k}={v}' for k, v in ref['locator'].items() if v is not None and k != 'kind')
                    citations.append(f'{ref["source_name"]} [{locator}]: {ref["quote"]}')
            payload = {'decision': result['decision'], 'response_en': result['response_en'], 'citations': '\n'.join(dict.fromkeys(citations))}
            for field, address in coordinate['outputs'].items():
                cells[address] = payload[field]
        target = self.repo.data_dir / 'exports' / f'{artifact["id"]}.xlsx'
        target.parent.mkdir(parents=True, exist_ok=True)
        patch_workbook(source, target, values)
        verify_fidelity(source, target, values)
        with self.repo.transaction():
            self._require_current(artifact)
            self.repo.update('export', artifact['id'], {'status': 'completed', 'managed_path': str(target.relative_to(self.repo.data_dir)), 'file_sha256': file_hash(target)})
            self.repo.audit(job['project_id'], 'export.completed', {'export_id': artifact['id'], 'snapshot_hash': artifact['snapshot_hash']})

    def download(self, export_id):
        from app.security.local_access import controlled_path
        artifact = self.repo.get('export', export_id)
        self._require_current(artifact)
        if artifact['status'] != 'completed':
            raise DomainError('EXPORT_NOT_READY', '导出文件尚未生成', 409)
        path = controlled_path(self.repo.data_dir, artifact['managed_path'])
        if not path.is_file() or file_hash(path) != artifact['file_sha256']:
            raise DomainError('EXPORT_FILE_CHANGED', '导出文件校验失败', 409)
        return path
