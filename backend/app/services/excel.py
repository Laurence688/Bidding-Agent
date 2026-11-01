from app.services.files import FileService
from app.excel.precheck import precheck
from app.excel.mapping import create_mapping
from app.domain.errors import DomainError


class ExcelService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def enqueue_precheck(self, project_id, file_id, session_id=None, product_id=None, task_note=""):
        FileService(self.repo).resolve(file_id, project_id)
        if session_id:self.repo.get_session(session_id, project_id)
        if product_id:
            from app.services.libraries import LibraryService
            product_id=LibraryService(self.repo,self.settings).resolve_product(product_id)
        with self.repo.transaction():
            record = self.repo.put('precheck', {'file_id': file_id, 'status': 'queued', 'session_id':session_id, 'product_id':product_id, 'task_note':task_note, 'latest_mapping_id':None}, project_id)
            job = self.repo.put('job', {'kind': 'precheck', 'precheck_id': record['id'], 'status': 'queued', 'cancel_requested': False}, project_id, record['id'])
            if session_id:
                file=self.repo.get('file',file_id,project_id)
                self.repo.put('session_message',{'role':'user','type':'excel_confirmation','text':file.get('original_name',file.get('name','Excel 附件')),'payload':{'precheck_id':record['id'],'task_note':task_note}},project_id,session_id)
            return {'precheck_id': record['id'], 'job_id': job['id'], 'status': 'queued'}

    def execute_precheck(self, job):
        record = self.repo.get('precheck', job['precheck_id'], job['project_id'])
        path = FileService(self.repo).resolve(record['file_id'], job['project_id'])
        self.repo.update('precheck', record['id'], {'status': 'completed', 'report': precheck(path)})

    def mapping(self, precheck_id, selection, expected_version=None):
        record = self.repo.get('precheck', precheck_id)
        if record['status'] != 'completed' or not record.get('report', {}).get('accepted'):
            raise DomainError('PRECHECK_NOT_ACCEPTED', '工作簿预检尚未通过', 409)
        if expected_version is not None and record['version']!=expected_version:raise DomainError('VERSION_CONFLICT','确认卡已变化，请重新读取',409)
        original_version=record['version']
        path = FileService(self.repo).resolve(record['file_id'], record['project_id'])
        payload = create_mapping(path, selection)
        payload['prior_scope'] = self.prior_scope(record['project_id'], payload)
        with self.repo.transaction():
            record=self.repo.get('precheck',precheck_id)
            if record['version']!=original_version:raise DomainError('VERSION_CONFLICT','确认卡已变化，请重新读取',409)
            if record.get('confirmed_task_id'):raise DomainError('CARD_CONFIRMED','确认卡已经开始处理',409)
            result=self.repo.put('mapping', {**payload, 'file_id': record['file_id'], 'precheck_id': precheck_id}, record['project_id'], precheck_id)
            card=self.repo.update('precheck',precheck_id,{'latest_mapping_id':result['id'],'selection_draft':selection},record['version'])
            return {**result,'precheck_version':card['version']}

    def prior_scope(self, project_id, mapping):
        from app.services.run_reports import RunReportService
        positions = {(c['coordinate']['sheet'], c['coordinate']['input_cell'], c['raw_text']) for c in mapping['clauses']}
        for run in reversed(self.repo.list('run', project_id, limit=100000)):
            if run.get('mode') != 'SOC_BATCH':
                continue
            task = self.repo.get('task', run['task_id'], project_id)
            if task['active_run_id'] != run['id']:
                continue
            snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], project_id)
            if snapshot.get('file_sha256') != mapping['file_sha256']:
                continue
            rows = RunReportService(self.repo, self.settings).clauses(run['id'])
            excluded = [r for r in rows if r.get('annotation') and not r['annotation_stale'] and r['annotation']['verdict'] == 'not_requirement'
                and ((r.get('coordinate') or {}).get('sheet'), (r.get('coordinate') or {}).get('input_cell'), r['raw_text']) in positions]
            if excluded:
                return {'task_id': task['id'], 'run_id': run['id'], 'excluded_count': len(excluded),
                        'cells': [r['coordinate']['input_cell'] for r in excluded]}
        return None
