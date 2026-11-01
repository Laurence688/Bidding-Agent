"""Independent document runs. No product lookup, retrieval, SOC state or approval."""
import copy
import json
import re
from pathlib import Path
import zipfile

from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, file_hash
from app.model_gateway.contracts import ModelRequest
from app.model_gateway.gateway import ModelGateway
from app.schemas.document_tasks import DocumentTaskCreate, TranslationResult, AnalysisResult, CATEGORIES
from app.security.execution_policy import policy_for, models_for
from app.security.local_access import controlled_path
from app.services.files import FileService
from app.services.translation_category import display_category
from app.workflows.document_tasks import (PARSER_VERSION, PROMPTS, parse_file, excel_metadata,
    segment_blocks, frozen_terms, translation_flags)

MODES = {'TRANSLATION', 'TENDER_ANALYSIS'}


class DocumentTaskService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def upload(self, project_id, name, content):
        record = FileService(self.repo).upload(project_id, name, content, 'document_task')
        try:
            preview = self.preview(project_id, record['id'])
        except DomainError:
            # Rejected files never become eligible task inputs.
            self.repo.update('file', record['id'], {'document_precheck': 'rejected'})
            raise
        return {**record, 'preview': preview}

    def preview(self, project_id, file_id):
        record = self.repo.get('file', file_id, project_id)
        if record['purpose'] != 'document_task' or record.get('document_precheck') == 'rejected':
            raise DomainError('FILE_PURPOSE', '请使用独立文档任务上传入口', 422)
        path = FileService(self.repo).resolve(file_id, project_id)
        try:
            if path.suffix.lower() == '.xlsx':
                return {'file_id': file_id, 'sha256': record['sha256'], 'sheets': excel_metadata(path), 'requires_scope': True}
            blocks, limitations = parse_file(path)
            return {'file_id': file_id, 'sha256': record['sha256'], 'blocks': len(blocks), 'sample': [{'location': b['location'], 'text': b['source'][:500]} for b in blocks[:8]], 'limitations': limitations}
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError('DOCUMENT_INVALID', '文档结构无法读取，请提供有效的 docx、文字型 PDF 或 xlsx', 422) from exc

    def enqueue(self, project_id, body, idempotency_key):
        body = DocumentTaskCreate.model_validate(body).model_dump()
        self.repo.get('project', project_id)
        if not body['confirmed']:
            raise DomainError('DOCUMENT_CONFIRM_REQUIRED', '请先确认处理范围、语言及所选 API 发送权限', 422)
        if body.get('session_id'):
            self.repo.get_session(body['session_id'],project_id)
            if body['mode']!='TRANSLATION' or body['file_ids']:
                raise DomainError('CHAT_TRANSLATION_TEXT_ONLY','会话中仅支持单条文本翻译，文件请使用独立标书翻译页面',422)
            if not body.get('product_id'):
                raise DomainError('PRODUCT_REQUIRED','会话翻译需要记录本次所选型号',422)
        elif body.get('reference_message_id') or body.get('reference_clause_id'):
            raise DomainError('CONTEXT_REFERENCE_INVALID','引用消息必须提供所属会话',422)
        if bool(body['raw_text'].strip()) == bool(body['file_ids']):
            raise DomainError('DOCUMENT_INPUT', '请选择粘贴文本或文件；二者不能同时提交或同时为空', 422)
        if len(body['file_ids']) != len(set(body['file_ids'])):
            raise DomainError('DOCUMENT_INPUT', '同一原件不能重复加入任务', 422)
        def create(db):
            policy = policy_for(self.settings, 'cloud')
            models = models_for(self.settings, 'cloud')
            provider = models['providers']['primary']
            if not models.get('active_generation_profile_id'):
                raise DomainError('MODEL_CONFIG_MISSING', '文档任务需要明确选择一个 API 档案', 409)
            if provider.get('key_disabled') or not self.settings.secrets.get(provider['api_key_env']):
                raise DomainError('MODEL_CONFIG_MISSING', '请先配置所选 API 的密钥', 409)
            from app.security.execution_policy import assert_endpoint_allowed
            assert_endpoint_allowed(policy, provider['base_url'], 'generation')
            # Leave generous room for schema, instructions, metadata and output expansion.
            available = provider['context_limit'] - provider['max_output_tokens'] - 6000
            max_chars = min(4000, provider['max_output_tokens'] // 2, available // 2)
            manifest=None;source_text=body['raw_text']
            if body.get('session_id'):
                from app.services.context_management import ContextService
                from app.services.prompts import PromptService
                context=ContextService(self.repo,self.settings)
                if context._session_busy(project_id,body['session_id']) and any(j.get('kind')=='context_compression' and j.get('session_id')==body['session_id'] and j.get('status') in {'queued','running'} for j in self.repo.list('job',project_id,limit=100000)):
                    raise DomainError('CONTEXT_COMPRESSION_BUSY','对话正在压缩，请稍后发送',409)
                manifest=context.freeze_history(project_id,body['session_id'],body['product_id'],models,PromptService(self.repo,self.settings).snapshot())
                manifest['references']=context.resolve_references(manifest,body['raw_text'],body.get('reference_message_id'),body.get('reference_clause_id'))
                if manifest['references']:source_text=manifest['references'][0]['text']
                if len(source_text)>12000:raise DomainError('INPUT_INVALID','会话翻译最多支持 12000 字符，请使用独立文件翻译',422)
            files, segments = [], []
            if source_text.strip():
                blocks = [{'source': source_text, 'location': '粘贴文本'}]
                files.append({'id': 'text', 'name': '粘贴文本', 'sha256': content_hash(source_text), 'terms': frozen_terms(blocks, body['target_language']), 'limitations': []})
                segments.extend(segment_blocks(blocks, max_chars, 'text', '粘贴文本'))
            for ident in body['file_ids']:
                file = self.repo.get('file', ident, project_id)
                if file['purpose'] != 'document_task' or file.get('document_precheck') == 'rejected':
                    raise DomainError('FILE_PURPOSE', '文件不是有效的独立文档任务原件', 422)
                path = FileService(self.repo).resolve(ident, project_id)
                try:
                    blocks, limitations = parse_file(path, body['excel_selections'].get(ident))
                except DomainError:
                    raise
                except Exception as exc:
                    raise DomainError('DOCUMENT_INVALID', '文档结构无法读取', 422) from exc
                files.append({'id': ident, 'name': file['name'], 'sha256': file['sha256'], 'selection': body['excel_selections'].get(ident), 'terms': frozen_terms(blocks, body['target_language']), 'limitations': limitations})
                segments.extend(segment_blocks(blocks, max_chars, ident, file['name']))
            if len(segments) > 100000:
                raise DomainError('DOCUMENT_SCOPE_TOO_LARGE', '分段超过 100000，请拆批；原文未截断', 422)
            snapshot = {'models': models, 'execution_policy': policy, 'prompts': copy.deepcopy(PROMPTS), 'parser_version': PARSER_VERSION,
                'input_kind': 'file' if body['file_ids'] else 'text', 'segment_version': 'source-offset-v1', 'files': files, 'segments': segments, 'target_language': body['target_language'], 'max_segment_chars': max_chars,
                'terminology_method': 'local-bilingual-tender-and-identifiers-v1'}
            if manifest is not None:snapshot.update(context_manifest=manifest,session_id=body['session_id'],product_id=body['product_id'],raw_text=body['raw_text'],resolved_source=source_text,continuity_prompts=PromptService(self.repo,self.settings).snapshot())
            config = self.repo.put('config_snapshot', {**snapshot, 'content_hash': content_hash(snapshot)}, project_id)
            task = self.repo.put('task', {'mode': body['mode'], 'kind': 'document_task', 'active_run_id': None,'session_id':body.get('session_id'),'product_id':body.get('product_id')}, project_id)
            return self._create_run(task, config)
        return self.repo.idempotent(project_id, 'document-task-create', idempotency_key, body, create)

    def _create_run(self, task, config, parent_run_id=None):
        run = self.repo.put('run', {'kind': 'document_task', 'mode': task['mode'], 'task_id': task['id'], 'config_snapshot_id': config['id'],
            'session_id':task.get('session_id'),'product_id':task.get('product_id'),'context_manifest':config.get('context_manifest'),
            'parent_run_id': parent_run_id, 'status': 'queued', 'artifact_revision': 0}, task['project_id'], task['id'])
        for index, segment in enumerate(config['segments']):
            self.repo.put('document_segment', {**segment, 'segment_index': index, 'segment_id': segment['id'], 'status': 'unprocessed', 'completed': False, 'result': None}, task['project_id'], run['id'])
        job = self.repo.put('job', {'kind': 'document_task', 'mode': task['mode'], 'task_id': task['id'], 'run_id': run['id'], 'status': 'queued',
            'cancel_requested': False, 'stage': 'document_prepare', 'processed_items': 0, 'total_items': len(config['segments'])}, task['project_id'], run['id'])
        self.repo.update('run', run['id'], {'job_id': job['id']})
        self.repo.update('task', task['id'], {'active_run_id': run['id']})
        self.repo.event(job['id'], 'job.status', {'status': 'queued', 'mode': task['mode']})
        if task.get('session_id'):
            self.repo.put('session_message',{'role':'user','type':'translation_input','text':config['raw_text'],'task_id':task['id'],'run_id':run['id'],'payload':{'target_language':config['target_language']}},task['project_id'],task['session_id'])
            self.repo.put('session_message',{'role':'assistant','type':'progress','text':'','task_id':task['id'],'run_id':run['id']},task['project_id'],task['session_id'])
        return {'task_id': task['id'], 'run_id': run['id'], 'job_id': job['id'], 'status': 'queued'}

    def _run(self, run_id):
        run = self.repo.get('run', run_id)
        if run.get('kind') != 'document_task' or run.get('mode') not in MODES:
            raise DomainError('DOCUMENT_RUN_REQUIRED', '此运行不属于独立文档任务', 404)
        if run.get('job_id'):
            job = self.repo.get('job', run['job_id'])
            from app.worker.leases import owner_alive
            if job['status'] == 'running' and job.get('owner_pid') and not owner_alive(job.get('owner_pid'), job.get('owner_started')):
                job = self.repo.update('job', job['id'], {'status': 'interrupted', 'error_code': 'WORKER_INTERRUPTED'})
                self.repo.event(job['id'], 'job.status', {'status': 'interrupted'})
            if job['status'] == 'interrupted' and run['status'] != 'interrupted':
                run = self.repo.update('run', run_id, {'status': 'interrupted'})
        return run

    def list_tasks(self, project_id):
        self.repo.get('project', project_id)
        tasks = [t for t in self.repo.list('task', project_id, limit=100000) if t.get('kind') == 'document_task']
        return {'items': [{**t, 'run': self._run(t['active_run_id']), 'display_category': display_category(t['mode'],self.repo.get('config_snapshot',self.repo.get('run',t['active_run_id'])['config_snapshot_id'],project_id))} for t in reversed(tasks)]}

    def detail(self, run_id):
        run = self._run(run_id)
        segments = self._segments(run_id)
        completed = sum(s['status'] == 'completed' for s in segments)
        failed = sum(s['status'] == 'failed' for s in segments)
        config = self.repo.get('config_snapshot', run['config_snapshot_id'], run['project_id'])
        artifacts = self.repo.list('document_artifact', run['project_id'], run_id, limit=1000)
        return {'run': run, 'display_category': display_category(run['mode'],config), 'job': self.repo.get('job', run['job_id']), 'segments': segments,
            'coverage': {'total': len(segments), 'completed': completed, 'failed': failed, 'unprocessed': len(segments) - completed - failed},
            'files': config['files'], 'target_language': config['target_language'], 'artifacts': [{k:v for k,v in a.items() if k != 'managed_path'} for a in artifacts if a['revision'] == run['artifact_revision']],
            'analysis': self._analysis(segments) if run['mode'] == 'TENDER_ANALYSIS' else None,
            'attempts': [{k: a.get(k) for k in ['attempt_id', 'clause_id', 'logical_node_name', 'provider', 'model', 'status', 'usage', 'latency', 'error_code']} for a in ModelGateway(self.repo, config['models'], {}).attempts(run_id=run_id)]}

    def _segments(self, run_id):
        return sorted(self.repo.list('document_segment', parent_id=run_id, limit=100000), key=lambda s: s['segment_index'])

    @staticmethod
    def _analysis(segments):
        categories = {key: [] for key in CATEGORIES}
        seen = set()
        for segment in segments:
            for item in (segment.get('result') or {}).get('items', []):
                key = (item['category'], item['summary'], item['quote'], segment['file_id'], segment['location'])
                if key not in seen:
                    seen.add(key)
                    categories[item['category']].append({**item, 'segment_id': segment['segment_id'], 'file_name': segment['file_name'], 'location': segment['location'], 'start': segment['start'], 'end': segment['end']})
        # All conflicting statements stay side by side with their original sources.
        return {'categories': categories, 'conflict_policy': '跨文件及不同位置条款并列保留，不自动认定版本优先；需人工核对冲突',
            'absence_scope': '空分类仅表示已处理文本未发现；失败、未处理及图片不代表没有要求'}

    def action(self, run_id, action):
        with self.repo.transaction():
            run = self._run(run_id)
            task = self.repo.get('task', run['task_id'], run['project_id'])
            job = self.repo.get('job', run['job_id'])
            if task['active_run_id'] != run_id:
                raise DomainError('RUN_SUPERSEDED', '历史运行只读，请打开当前运行', 409)
            if action == 'cancel':
                if job['status'] not in {'queued', 'running', 'interrupted'}:
                    raise DomainError('JOB_STATE', '当前任务不可取消', 409)
                status = 'cancelled' if job['status'] != 'running' else job['status']
                updated = self.repo.update('job', job['id'], {'cancel_requested': True, 'status': status})
                if status == 'cancelled':
                    self.repo.update('run', run_id, {'status': status})
                return updated
            if job['status'] in {'queued', 'running'}:
                raise DomainError('JOB_STATE', '正在处理，请等待或取消后操作', 409)
            config = self.repo.get('config_snapshot', run['config_snapshot_id'])
            if action == 'rerun':
                # New run freezes current selected model/policy and retains explicit input/language.
                snapshot = {k:v for k,v in config.items() if k not in {'id','project_id','parent_id','version','created_at','updated_at','content_hash'}}
                snapshot.update(models=models_for(self.settings, 'cloud'), execution_policy=policy_for(self.settings, 'cloud'), prompts=copy.deepcopy(PROMPTS))
                fresh = self.repo.put('config_snapshot', {**snapshot, 'content_hash': content_hash(snapshot)}, run['project_id'])
                return self._create_run(task, fresh, run_id)
            if action not in {'retry', 'resume'}:
                raise DomainError('DOCUMENT_ACTION', '未知操作', 422)
            if action == 'resume' and job['status'] not in {'interrupted', 'cancelled'}:
                raise DomainError('JOB_STATE', '恢复仅适用于中断或取消的运行', 409)
            if action == 'retry' and job['status'] not in {'failed', 'partial'}:
                raise DomainError('JOB_STATE', '重试仅适用于失败或部分完成的运行', 409)
            segments = self._segments(run_id)
            pending = [s for s in segments if s['status'] != 'completed' and not s.get('unsupported') and (action != 'retry' or s['status'] == 'failed' or run.get('error_code'))]
            attempts = ModelGateway(self.repo, config['models'], {}).attempts(run_id=run_id)
            pending = [s for s in pending if sum(a['clause_id'] == s['segment_id'] for a in attempts) < 2]
            finalize_only = action == 'resume' and any(s['completed'] for s in segments) and all(s['completed'] or s.get('unsupported') for s in segments)
            if not pending and not run.get('artifact_error') and not finalize_only:
                raise DomainError('ATTEMPT_BUDGET_EXHAUSTED', '原运行无可执行分段或调用额度已耗尽；请选择重新运行创建新额度', 409)
            for segment in pending:
                self.repo.update('document_segment', segment['id'], {'status': 'unprocessed', 'error_code': None})
            self.repo.update('run', run_id, {'status': 'queued'})
            job = self.repo.update('job', job['id'], {'status': 'queued', 'cancel_requested': False})
            self.repo.event(job['id'], 'job.status', {'status': 'queued', 'action': action})
            return {'run_id': run_id, 'task_id': task['id'], 'job_id': job['id'], 'status': 'queued'}

    def execute(self, job, transport=None):
        from app.services.tasks import TaskService
        TaskService(self.repo,self.settings).assert_active(job['run_id'])
        try:
            return self._execute(job, transport)
        except Exception as exc:
            from app.services.activity import ActivityService
            ActivityService(self.repo).finish(job['run_id'])
            # Keep committed results and never let a worker's generic finish hide failure.
            run = self._run(job['run_id'])
            view = self.detail(run['id'])
            status = 'cancelled' if self.repo.get('job', job['id']).get('cancel_requested') else ('interrupted' if getattr(exc,'code',None)=='EXECUTION_OFFLINE' else ('partial' if view['coverage']['completed'] else 'failed'))
            code = exc.code if isinstance(exc, DomainError) else 'DOCUMENT_EXECUTION_FAILED'
            with self.repo.transaction():
                self.repo.update('run', run['id'], {'status': status, 'error_code': code})
                self.repo.update('job', job['id'], {'status': status, 'error_code': code, 'stage': 'document_failed', 'coverage': view['coverage']})
                self.repo.event(job['id'], 'job.status', {'status': status, 'error_code': code, 'coverage': view['coverage']})
            if view['coverage']['completed'] and run.get('session_id'):self._chat_result(run)
            try:
                self._artifacts(run['id'])
            except Exception:
                # Failed artifact generation stays explicit and may be resumed/retried.
                self.repo.update('run', run['id'], {'artifact_error': 'DOCUMENT_ARTIFACT_FAILED'})
            return {'status': status, 'error_code': code, 'coverage': view['coverage'], 'run_id': run['id']}

    def _execute(self, job, transport=None):
        from app.services.tasks import TaskService
        run = TaskService(self.repo,self.settings).assert_active(job['run_id'])
        config = self.repo.get('config_snapshot', run['config_snapshot_id'], run['project_id'])
        for file in config['files']:
            if file['id'] != 'text':
                FileService(self.repo).resolve(file['id'], run['project_id'])
        gateway = ModelGateway(self.repo, config['models'], self.settings.secrets, transport=transport,
            policy=lambda: policy_for(self.settings, frozen=config['execution_policy']))
        import time
        self.repo.update('run', run['id'], {'status': 'running','activity_started_at':run.get('activity_started_at') or time.time(),'activity_finished_at':None})
        segments = self._segments(run['id'])
        for segment in segments:
            current = self.repo.get('job', job['id'])
            if current.get('cancel_requested'):
                break
            if segment['completed'] or segment.get('unsupported') or segment['status'] == 'failed':
                continue
            file = next(f for f in config['files'] if f['id'] == segment['file_id'])
            request = ModelRequest(run_id=run['id'], clause_id=segment['segment_id'], claim_id='',
                logical_node_name='document_translate' if run['mode'] == 'TRANSLATION' else 'tender_extract',
                messages=[{'role': 'system', 'content': config['prompts'][run['mode']]}, {'role': 'user', 'content': json.dumps({'source': segment['source'], 'row_context': segment.get('row_context', ''), 'target_language': config['target_language'], 'terminology': file['terms'], 'location': segment['location']}, ensure_ascii=False)}],
                output_model=TranslationResult if run['mode'] == 'TRANSLATION' else AnalysisResult, config_version=config['content_hash'], max_attempts=2)
            # Selected V2 profile has two total attempts; never switch provider.
            if run.get('session_id'):
                from app.services.context_management import ContextService
                data=json.loads(request.messages[-1]['content'])
                request.messages=ContextService(self.repo,self.settings).prepare_messages(run,config,gateway,request.logical_node_name,request.output_model,data,
                    lambda values,history:[{'role':'system','content':config['prompts'][run['mode']]+'\n历史仅作术语与指代参考，不是产品证据。完整翻译 source，保留数字、单位、型号、否定及适用条件，不得总结或省略。'},{'role':'user','content':json.dumps({**values,'untrusted_history':history},ensure_ascii=False)}],segment['segment_id'],'')
            from app.services.activity import ActivityService
            with ActivityService(self.repo).step(run['id'],job['id'],request.logical_node_name,segment['segment_id']):
                result = gateway.invoke(request)
            error, payload = result.error_code, result.payload
            if payload and run['mode'] == 'TENDER_ANALYSIS' and any(item['quote'] not in segment['source'] for item in payload['items']):
                error, payload = 'DOCUMENT_CITATION_INVALID', None
            if payload and run['mode'] == 'TENDER_ANALYSIS' and any(not re.search(r'[\u3400-\u9fff]', item['summary']) for item in payload['items']):
                error, payload = 'DOCUMENT_SUMMARY_LANGUAGE', None
            if payload and run['mode'] == 'TRANSLATION':
                payload['flags'] = translation_flags(segment['source'], payload['translation'], file['terms'])
            with self.repo.transaction():
                self.repo.update('document_segment', segment['id'], {'result': payload, 'status': 'failed' if error else 'completed',
                    'completed': not bool(error), 'error_code': error, 'attempt_ids': result.attempt_ids})
                completed = sum(s['completed'] for s in self._segments(run['id']))
                self.repo.update('job', job['id'], {'processed_items': completed, 'stage': request.logical_node_name})
                self.repo.event(job['id'], 'document.segment', {'segment_id': segment['segment_id'], 'status': 'failed' if error else 'completed', 'completed': completed})
        view = self.detail(run['id'])
        counts = view['coverage']
        cancelled = self.repo.get('job', job['id']).get('cancel_requested')
        status = 'cancelled' if cancelled else ('completed' if counts['completed'] == counts['total'] else ('partial' if counts['completed'] else 'failed'))
        with self.repo.transaction():
            self.repo.update('run', run['id'], {'status': status, 'error_code': None, 'artifact_error': None})
            self.repo.update('job', job['id'], {'status': status, 'stage': 'document_done', 'coverage': counts})
            self.repo.event(job['id'], 'job.status', {'status': status, 'coverage': counts})
        from app.services.activity import ActivityService
        ActivityService(self.repo).finish(run['id'])
        if counts['completed'] and run.get('session_id'):
            self._chat_result(run)
        self._artifacts(run['id'])
        return {'status': status, 'coverage': counts, 'run_id': run['id']}

    def _chat_result(self,run):
        rows=[row for row in self._segments(run['id']) if row.get('completed') and row.get('result')]
        if not rows:return
        run=self.repo.get('run',run['id']);text='\n'.join(row['result']['translation'] for row in rows)
        incomplete=run.get('status')!='completed'
        flags=[f for row in rows for f in row['result'].get('flags',[])]
        if incomplete:flags.insert(0,'部分完成：仅显示已完成译文，仍有未处理或失败分段')
        payload={'result':{'translation':text,'flags':flags,'incomplete':incomplete}}
        with self.repo.transaction():
            existing=[m for m in self.repo.list('session_message',run['project_id'],run['session_id'],limit=100000) if m.get('run_id')==run['id'] and m.get('type')=='translation_result']
            if not existing:self.repo.put('session_message',{'role':'assistant','type':'translation_result','text':text,'payload':payload,'run_id':run['id'],'task_id':run['task_id']},run['project_id'],run['session_id'])
            elif existing[-1].get('payload')!=payload:self.repo.update('session_message',existing[-1]['id'],{'text':text,'payload':payload})

    def _artifacts(self, run_id):
        if self.repo.get('run',run_id).get('session_id'):return
        view = self.detail(run_id)
        run, segments = view['run'], view['segments']
        # Idempotent for the exact committed segment/result/status set.
        digest = content_hash({'status': run['status'], 'error_code': run.get('error_code'), 'segments': [{k:s.get(k) for k in ['id','status','result']} for s in segments]})
        if run.get('artifact_hash') == digest:
            return
        incomplete = run['status'] != 'completed'
        marker = '-不完整' if incomplete else ''
        revision = run['artifact_revision'] + 1
        folder = controlled_path(self.repo.data_dir, f'document-artifacts/{run_id}/{revision}')
        folder.mkdir(parents=True, exist_ok=True)
        built = []
        def add(path, name):
            built.append({'name': name, 'managed_path': str(path.relative_to(self.repo.data_dir)), 'sha256': file_hash(path), 'revision': revision, 'incomplete': incomplete})
        if run['mode'] == 'TENDER_ANALYSIS':
            from docx import Document
            doc = Document(); doc.add_heading('标书拆解报告' + ('（不完整／部分完成）' if incomplete else ''), 0)
            doc.add_paragraph('覆盖：' + json.dumps(view['coverage'], ensure_ascii=False))
            doc.add_paragraph(view['analysis']['absence_scope']); doc.add_paragraph(view['analysis']['conflict_policy'])
            for category, items in view['analysis']['categories'].items():
                doc.add_heading(category, 1)
                if not items:
                    doc.add_paragraph('已处理文本未发现此项；未处理内容不作判断。')
                for item in items:
                    doc.add_paragraph(item['summary']); doc.add_paragraph('原文：' + item['quote'])
                    doc.add_paragraph(f"来源：{item['file_name']} · {item['location']} · 字符 {item['start']}–{item['end']}")
            self._append_coverage(doc, view)
            path = folder / 'report.docx'; doc.save(path); add(path, '标书拆解报告' + marker + '.docx')
        else:
            for index, file in enumerate(view['files']):
                rows = [s for s in segments if s['file_id'] == file['id']]
                stem = Path(file['name']).stem
                if Path(file['name']).suffix.lower() == '.xlsx':
                    from openpyxl import Workbook
                    wb = Workbook(); sheet = wb.active; sheet.title = '双语对照'
                    sheet.append(['原文位置', '原文', '译文', '状态与异常'])
                    for segment in rows:
                        payload = segment.get('result') or {}
                        sheet.append([segment['location'], segment['source'], payload.get('translation', ''), segment['status'] + '；' + '；'.join(payload.get('flags', [])) + (segment.get('unsupported') or '')])
                    meta = wb.create_sheet('覆盖与说明'); meta.append(['状态', '不完整／部分完成' if incomplete else '全部完成']); meta.append(['覆盖', json.dumps(view['coverage'], ensure_ascii=False)])
                    for note in file.get('limitations', []): meta.append(['限制', note])
                    meta.append(['冻结术语', json.dumps(file['terms'], ensure_ascii=False)])
                    for worksheet in wb:
                        for cells in worksheet:
                            for cell in cells:
                                if isinstance(cell.value,str):cell.data_type='s'
                    path = folder / f'{index}.xlsx'; wb.save(path); add(path, f'{index + 1}-{stem}-双语{marker}.xlsx')
                else:
                    from docx import Document
                    doc = Document(); doc.add_heading(stem + ' 双语对照' + ('（不完整／部分完成）' if incomplete else ''), 0)
                    doc.add_paragraph('不承诺复刻原排版。冻结术语：' + json.dumps(file['terms'], ensure_ascii=False))
                    for segment in rows:
                        payload = segment.get('result') or {}
                        doc.add_heading(f"{segment['location']} · 字符 {segment['start']}–{segment['end']}", 2)
                        doc.add_paragraph('原文：' + segment['source']); doc.add_paragraph('译文：' + payload.get('translation', '【尚未完成】'))
                        doc.add_paragraph('状态：' + segment['status'] + '；' + '；'.join(payload.get('flags', [])) + (segment.get('unsupported') or ''))
                    self._append_coverage(doc, view)
                    path = folder / f'{index}.docx'; doc.save(path); add(path, f'{index + 1}-{stem}-双语{marker}.docx')
        if len(built) > 1:
            path = folder / 'bundle.zip'
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
                for item in built:
                    output.write(controlled_path(self.repo.data_dir, item['managed_path']), item['name'])
                output.writestr('处理清单.txt', ('不完整／部分完成\n' if incomplete else '全部完成\n') + json.dumps(view['coverage'], ensure_ascii=False))
            add(path, '双语文件' + marker + '.zip')
        with self.repo.transaction():
            for artifact in built:
                self.repo.put('document_artifact', artifact, run['project_id'], run_id)
            self.repo.update('run', run_id, {'artifact_revision': revision, 'artifact_hash': digest})

    @staticmethod
    def _append_coverage(doc, view):
        doc.add_heading('覆盖、限制与未完成清单', 1)
        doc.add_paragraph(json.dumps(view['coverage'], ensure_ascii=False))
        if view['run'].get('error_code'):
            doc.add_paragraph('运行异常：' + view['run']['error_code'])
        for file in view['files']:
            for note in file.get('limitations', []): doc.add_paragraph(file['name'] + '：' + note)
        for segment in view['segments']:
            if segment['status'] != 'completed':
                doc.add_paragraph(f"{segment['file_name']} · {segment['location']} · {segment['start']}–{segment['end']}：{segment['status']} {segment.get('error_code') or segment.get('unsupported') or '尚未处理'}")

    def artifact_path(self, artifact_id):
        artifact = self.repo.get('document_artifact', artifact_id)
        self._run(artifact['parent_id'])
        path = controlled_path(self.repo.data_dir, artifact['managed_path'])
        if not path.is_file() or file_hash(path) != artifact['sha256']:
            raise DomainError('ARTIFACT_CHANGED', '产物缺失或已变化，请重新生成', 409)
        return path
