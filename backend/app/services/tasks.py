from app.evaluation.provenance import provenance
from app.evaluation.state import set_terminal
from app.domain.errors import DomainError
from app.domain.identifiers import new_id, content_hash
from app.services.prompts import PromptService
from app.services.product_catalog import response_product_name
from app.services.knowledge import KnowledgeService
from app.services.files import FileService
from app.excel.requirements import build_requirement_clauses


class TaskService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def config_snapshot(self, product_id, project_id=None, model_mode=None):
        from app.workflows.soc_graph import SOC_WORKFLOW_VERSION
        from app.security.execution_policy import policy_for, models_for
        policy = policy_for(self.settings, model_mode)
        if policy['model_mode']=='cloud' and self.settings.models.get('active_generation_profile_id'):
            provider=models_for(self.settings,'cloud')['providers']['primary']
            if provider.get('key_disabled') or not self.settings.secrets.get(provider['api_key_env']):
                raise DomainError('MODEL_CONFIG_MISSING','请先在模型设置中保存所选供应商的 API 密钥',409)
        knowledge = KnowledgeService.from_settings(self.repo, self.settings)
        from app.services.libraries import LibraryService
        libraries=LibraryService(self.repo,self.settings)
        active = libraries.task_snapshot(product_id,project_id)
        def embedding_endpoints(snapshot):
            endpoints = []
            profile = snapshot.get('embedding_profile', {})
            if profile.get('mode') == 'cloud':
                endpoints.append(profile['base_url'])
            for ident in snapshot.get('child_snapshot_ids', []):
                endpoints.extend(embedding_endpoints(self.repo.get('knowledge_snapshot', ident)))
            return endpoints
        policy['allowed_services']['embedding'] = embedding_endpoints(active) if active else []
        if active and (policy['model_mode'] == 'local' or policy.get('embedding_local_only')):
            def validate_local(snapshot):
                if snapshot.get('embedding_profile', {}).get('mode') == 'cloud':
                    raise DomainError('LOCAL_INDEX_REQUIRED', '本地模式需要绑定本地 Embedding 索引版本，未发送查询', 409)
                for ident in snapshot.get('child_snapshot_ids', []):
                    validate_local(self.repo.get('knowledge_snapshot', ident))
            validate_local(active)
        payload = {'models': models_for(self.settings, policy['model_mode']), 'execution_policy': policy, 'prompts': PromptService(self.repo, self.settings).snapshot(), 'workflow_version': SOC_WORKFLOW_VERSION,
            'processing': self.settings.processing,
            'product_naming_version':1, 'response_product_name':response_product_name(next((p for p in libraries.products() if p['id']==product_id),{})),
            'provenance': provenance(self.settings.root),
            'products': libraries.products(), 'retrieval': {key: self.settings.local[key] for key in ['retrieval_top_k', 'rrf_k', 'embedding_path', 'embedding_device', 'embedding_max_length', 'embedding_batch_size']},
            'knowledge_snapshot_id': active['id'] if active else None,
            'library_versions': active.get('library_versions',[]) if active else []}
        return self.repo.put('config_snapshot', {**payload, 'content_hash': content_hash(payload)})

    def enqueue(self, project_id, body, idempotency_key):
        self.repo.assert_project_active(project_id)
        try:
            from app.services.libraries import LibraryService
            product = LibraryService(self.repo,self.settings).resolve_product(body.get('product_id'))
        except ValueError as exc:
            raise DomainError('PRODUCT_REQUIRED', '请先选择任务型号', 422) from exc
        mode = body.get('mode')
        if mode not in {'SOC_SINGLE', 'SOC_BATCH', 'PRODUCT_QA'}:
            raise DomainError('MODE_INVALID', '请明确选择问答、单条 SOC 或 Excel 批任务', 422)
        if (body.get('reference_message_id') or body.get('reference_clause_id')) and not body.get('session_id'):
            raise DomainError('CONTEXT_REFERENCE_INVALID','引用必须提供所属会话',422)
        if body.get('session_id'):
            self.repo.get_session(body['session_id'], project_id)
        def create(db):
            mapping = None
            if mode == 'SOC_BATCH':
                mapping = self.repo.get('mapping', body.get('mapping_id'), project_id)
                original = build_requirement_clauses(FileService(self.repo).resolve(mapping['file_id'], project_id),
                    mapping['clauses'], mapping['selection'], excluded=mapping.get('excluded', []), expected_sha=mapping['file_sha256'])
                clauses = [{**row, 'id': new_id()} for row in original]
            else:
                text = body.get('raw_text', '').strip()
                if not text or len(text) > 12000:
                    raise DomainError('INPUT_INVALID', '请输入 1 至 12000 字符的原文', 422)
                clauses = [{'id': new_id(), 'raw_text': text}]
            snapshot_data = {'clauses': clauses, 'raw_text': body.get('raw_text', ''), 'mapping_id': mapping['id'] if mapping else None,
                'file_id': mapping['file_id'] if mapping else None, 'file_sha256': mapping['file_sha256'] if mapping else None}
            if mapping:
                snapshot_data.update(excluded=mapping.get('excluded', []), header_targets=mapping.get('header_targets', {}))
            snapshot = self.repo.put('input_snapshot', {**snapshot_data, 'content_hash': content_hash(snapshot_data)}, project_id)
            config = self.config_snapshot(product,project_id,body.get('model_mode'))
            task = self.repo.put('task', {'mode': mode, 'session_id': body.get('session_id'), 'product_id': product, 'active_run_id': None, 'task_note':body.get('task_note',''),'reference_message_id':body.get('reference_message_id'),'reference_clause_id':body.get('reference_clause_id')}, project_id)
            accepted = self._create_run(task, snapshot, config, product, None, {})
            if body.get('session_id'):
                self.repo.put('session_message', {'role': 'user', 'type':{'PRODUCT_QA':'qa_input','SOC_SINGLE':'soc_input','SOC_BATCH':'batch_input'}[mode], 'text': body.get('raw_text') or f'Excel 批任务：{len(clauses)} 条',
                    'task_id': task['id'], 'run_id': accepted['run_id']}, project_id, body['session_id'])
                self.repo.put('session_message',{'role':'assistant','type':'progress','text':'','task_id':task['id'],'run_id':accepted['run_id']},project_id,body['session_id'])
            return accepted
        return self.repo.idempotent(project_id, 'create_task', idempotency_key, body, create)

    def _create_run(self, task, snapshot, config, product, parent_run_id, inherited):
        if task.get('session_id'):
            active=[j for j in self.repo.list('job',task['project_id'],limit=100000) if j.get('kind')=='context_compression' and j.get('session_id')==task['session_id'] and j.get('status') in {'queued','running'}]
            if active:raise DomainError('CONTEXT_COMPRESSION_BUSY','对话正在压缩，请完成后再发送问题',409)
        from app.services.context_management import ContextService
        manifest=ContextService(self.repo,self.settings).freeze_history(task['project_id'],task.get('session_id'),product,config['models'],config['prompts'])
        if task.get('session_id'):
            manifest['references']=ContextService(self.repo,self.settings).resolve_references(manifest,snapshot.get('raw_text',''),task.get('reference_message_id'),task.get('reference_clause_id'))
            payload={k:v for k,v in config.items() if k not in {'id','project_id','parent_id','version','created_at','updated_at','content_hash'}}
            payload['context_manifest']=manifest
            config=self.repo.put('config_snapshot',{**payload,'content_hash':content_hash(payload)},task['project_id'])
        run = self.repo.put('run', {'task_id': task['id'], 'session_id': task.get('session_id'), 'mode': task['mode'],
            'product_id': product, 'input_snapshot_id': snapshot['id'], 'config_snapshot_id': config['id'],
            'knowledge_snapshot_id': config['knowledge_snapshot_id'], 'parent_run_id': parent_run_id,
            **({'context_manifest':manifest,**({'qa_context_manifest':manifest} if task['mode']=='PRODUCT_QA' else {})} if manifest is not None else {})}, task['project_id'], task['id'])
        for clause in snapshot['clauses']:
            old = inherited.get(clause['id'])
            self.repo.put('run_binding', {'clause_id': clause['id'], 'raw_text': clause['raw_text'], 'coordinate': clause.get('coordinate'),
                'source_context': clause.get('source_context', {}),
                'result_id': old.get('result_id') if old else None, 'review_status': old.get('review_status', 'pending') if old else 'pending',
                'initial_result_id': old.get('result_id') if old else None,
                'approved_review_id': old.get('approved_review_id') if old else None,
                'inherited_from_run_id': parent_run_id if old else None,
                'inheritance_hash': content_hash({'clause': clause, 'config': config['content_hash'], 'product': product})}, task['project_id'], run['id'])
        self.repo.update('task', task['id'], {'active_run_id': run['id'], 'product_id': product})
        job = self.repo.put('job', {'kind': 'task', 'run_id': run['id'], 'task_id': task['id'], 'status': 'queued',
            'cancel_requested': False, 'stage': 'prepare', 'processed_items': 0, 'total_items': len(snapshot['clauses'])}, task['project_id'], run['id'])
        self.repo.event(job['id'], 'job.status', {'status': 'queued'})
        if parent_run_id and task.get('session_id'):
            self.repo.put('session_message', {'role':'assistant','type':'progress','text':'已重新排队，正在处理。','payload':{'parent_run_id':parent_run_id},'task_id':task['id'],'run_id':run['id']},task['project_id'],task['session_id'])
        return {'job_id': job['id'], 'task_id': task['id'], 'run_id': run['id'], 'status': 'queued',
                'status_url': f'/api/v1/jobs/{job["id"]}', 'events_url': f'/api/v1/jobs/{job["id"]}/events'}

    def assert_active(self, run_id):
        run = self.repo.get('run', run_id)
        task = self.repo.get('task', run['task_id'], run['project_id'])
        if task['active_run_id'] != run_id:
            raise DomainError('RUN_SUPERSEDED', '这是历史运行，仅可查看；请打开当前运行', 409)
        return run

    def refresh_batch_snapshot(self, snapshot):
        """Enrich a new execution's context without mutating any historical snapshot."""
        if not snapshot.get('mapping_id'):
            return snapshot
        mapping = self.repo.get('mapping', snapshot['mapping_id'], snapshot['project_id'])
        path = FileService(self.repo).resolve(snapshot['file_id'], snapshot['project_id'])
        clauses = build_requirement_clauses(path, snapshot['clauses'], mapping['selection'],
            excluded=snapshot.get('excluded', []), expected_sha=snapshot['file_sha256'])
        if clauses == snapshot['clauses']:
            return snapshot
        payload = {k: snapshot[k] for k in ['raw_text', 'mapping_id', 'file_id', 'file_sha256', 'lifecycle_context', 'excluded', 'header_targets'] if k in snapshot}
        payload.update(clauses=clauses, parent_input_snapshot_id=snapshot['id'])
        return self.repo.put('input_snapshot', {**payload, 'content_hash': content_hash(payload)}, snapshot['project_id'])

    def new_run(self, task_id, expected_version, base_run_id, changes=None, product_id=None):
        changes = changes or {}
        with self.repo.transaction():
            task = self.repo.get('task', task_id)
            if task['version'] != expected_version or task['active_run_id'] != base_run_id:
                raise DomainError('VERSION_CONFLICT', '当前运行已变化，请刷新', 409)
            base = self.repo.get('run', base_run_id, task['project_id'])
            snapshot = self.repo.get('input_snapshot', base['input_snapshot_id'], task['project_id'])
            if not set(changes).issubset({c['id'] for c in snapshot['clauses']}):
                raise DomainError('CLAUSE_NOT_FOUND', '修改条款不属于任务范围', 422)
            from app.services.libraries import LibraryService
            product = LibraryService(self.repo,self.settings).resolve_product(product_id or base['product_id'])
            snapshot = self.refresh_batch_snapshot(snapshot)
            config = self.config_snapshot(product,task['project_id'])
            old_config = self.repo.get('config_snapshot', base['config_snapshot_id'])
            same_config = config['content_hash'] == old_config['content_hash'] and product == base['product_id'] and snapshot['id'] == base['input_snapshot_id']
            inherited = {}
            if same_config:
                for binding in self.repo.list('run_binding', parent_id=base_run_id, limit=100000):
                    if binding['clause_id'] not in changes and binding.get('result_id') and self._sources_valid(binding['result_id']):
                        inherited[binding['clause_id']] = binding
            clauses = []
            for clause in snapshot['clauses']:
                if clause['id'] in changes:
                    text = str(changes[clause['id']]).strip()
                    if not text or len(text) > 12000:
                        raise DomainError('INPUT_INVALID', '修改后的条款长度不合法', 422)
                    clauses.append({**clause, 'id': new_id(), 'raw_text': text})
                else:
                    clauses.append(clause)
            if changes:
                payload = {k: snapshot[k] for k in ['raw_text', 'mapping_id', 'file_id', 'file_sha256']}
                payload['clauses'] = clauses
                # Excel 原文不能私下修改并回填到不同原件，需重新映射新文件。
                if snapshot.get('mapping_id'):
                    raise DomainError('EXCEL_INPUT_IMMUTABLE', 'Excel 原文变化需要重新上传并确认映射', 422)
                snapshot = self.repo.put('input_snapshot', {**payload, 'content_hash': content_hash(payload)}, task['project_id'])
            return self._create_run(task, snapshot, config, product, base_run_id, inherited)

    def _sources_valid(self, result_id):
        result = self.repo.get('clause_result', result_id)['result']
        if result['decision'] == 'UNKNOWN' or not result['output_valid']:
            return False
        return all(self.repo.get('source_version', ref['source_version_id'])['status'] == 'valid' for a in result['assessments'] for ref in a['evidence_refs'])

    def cancel(self, job_id, expected_version):
        with self.repo.transaction():
            job = self.repo.get('job', job_id)
            if job['status'] not in {'queued', 'running', 'interrupted', 'waiting_review'}:
                raise DomainError('JOB_STATE', '此作业当前不能取消', 409)
            result = self.repo.update('job', job_id, {'cancel_requested': True, 'status': 'cancelled' if job['status'] != 'running' else 'running'}, expected_version)
            if job.get('evaluation_run_id') and result['status'] == 'cancelled':
                set_terminal(self.repo, job['evaluation_run_id'], 'cancelled')
            self.repo.event(job_id, 'job.status', {'status': result['status'], 'cancel_requested': True})
            return result

    def resume(self, job_id, expected_version):
        with self.repo.transaction():
            job = self.repo.get('job', job_id)
            if job.get('project_id'):
                self.repo.assert_project_active(job['project_id'])
            if job['status'] != 'interrupted':
                raise DomainError('JOB_STATE', '仅中断作业可恢复；修改事实请创建新运行', 409)
            if job.get('run_id'):
                self.assert_active(job['run_id'])
            if job.get('kind')=='knowledge':
                from app.services.libraries import LibraryService
                LibraryService(self.repo,self.settings).check_legacy(job['product_id'])
            result = self.repo.update('job', job_id, {'status': 'queued', 'cancel_requested': False, 'error': None}, expected_version)
            if job.get('evaluation_run_id'):
                self.repo.update('evaluation_run', job['evaluation_run_id'], {'status': 'queued'})
            self.repo.event(job_id, 'job.status', {'status': 'queued'})
            return result

