"""Typed conversation views and durable, exactly-once Excel confirmation cards."""
from app.domain.errors import DomainError


class ConversationService:
    def __init__(self, repo, settings):
        self.repo,self.settings=repo,settings

    def card(self, precheck_id):
        card=self.repo.get('precheck',precheck_id)
        jobs=[j for j in self.repo.list('job',card['project_id'],card['id']) if j.get('kind')=='precheck']
        job=jobs[-1] if jobs else None
        view={**card,'job':job}
        if job and job['status'] in {'failed','cancelled','interrupted'}:
            view.update(status=job['status'],error=job.get('error'))
        return view

    def selection(self, precheck_id, expected_version, selection):
        with self.repo.transaction():
            card=self.repo.get('precheck',precheck_id)
            if card.get('confirmed_task_id'):raise DomainError('CARD_CONFIRMED','确认卡已经开始处理',409)
            return self.repo.update('precheck',precheck_id,{'selection_draft':selection if selection is not None else card.get('selection_draft'),'latest_mapping_id':None},expected_version)

    def confirm(self, precheck_id, expected_version, mapping_id):
        from app.services.tasks import TaskService
        with self.repo.transaction():
            card=self.repo.get('precheck',precheck_id)
            if card.get('accepted'):
                if mapping_id!=card.get('latest_mapping_id'):raise DomainError('CARD_CONFIRMED','确认卡已经开始处理',409)
                return card['accepted']
            if card['version']!=expected_version:raise DomainError('VERSION_CONFLICT','确认卡已变化，请重新读取',409)
            mapping=self.repo.get('mapping',mapping_id,card['project_id'])
            if mapping.get('precheck_id')!=precheck_id or card.get('latest_mapping_id')!=mapping_id:
                raise DomainError('MAPPING_STALE','映射已失效，请重新校验',409)
            if card['status']!='completed' or not card.get('report',{}).get('accepted'):
                raise DomainError('PRECHECK_NOT_ACCEPTED','预检尚未通过',409)
            if not card.get('session_id') or not card.get('product_id'):raise DomainError('CARD_CONTEXT_REQUIRED','确认卡需要会话和型号',422)
            self.repo.get_session(card['session_id'],card['project_id'])
            accepted=TaskService(self.repo,self.settings).enqueue(card['project_id'],{'mode':'SOC_BATCH','mapping_id':mapping_id,
                'session_id':card['session_id'],'product_id':card['product_id'],'task_note':card.get('task_note','')},'confirmation:'+precheck_id)
            self.repo.update('precheck',precheck_id,{'confirmed_task_id':accepted['task_id'],'confirmed_run_id':accepted['run_id'],'accepted':accepted},expected_version)
            return accepted

    def qa_history(self, session_id, product_id=None, manifest=None):
        from app.services.context_management import ContextService
        service=ContextService(self.repo,self.settings)
        if manifest is None:
            session=self.repo.get_session(session_id)
            if not product_id:return []
            manifest=service.freeze_history(session['project_id'],session_id,product_id)
        return service.history(manifest)

    def messages(self, session_id, before=None, limit=50, cursor=None, run_id=None):
        session=self.repo.get_session(session_id)
        with self.repo.connection() as db:
            raw=[{**self.repo.decode(row),'order_key':[row['sequence'],0]} for row in db.execute(
                "SELECT r.*,o.sequence FROM records r JOIN session_message_order o ON o.message_id=r.id WHERE r.kind='session_message' AND r.user_id=? AND r.project_id=? AND r.parent_id=? AND r.deleted=0 ORDER BY o.sequence",
                (self.repo.local_user_id,session['project_id'],session_id))]
        users={m['run_id']:m for m in raw if m.get('run_id') and m.get('role')=='user'}
        groups={};rows=[];aliases={}
        for m in raw:
            if m.get('run_id') and m.get('role')=='assistant' and m.get('type') in {'progress','qa_result','soc_result','batch_result','translation_result'}:
                groups.setdefault(m['run_id'],[]).append(m)
            else:rows.append(m)
        for rid,group in groups.items():
            finals=[m for m in group if m.get('type')!='progress']
            chosen=dict((finals or group)[-1]);anchor=users.get(rid,group[0])
            chosen['order_key']=[anchor['order_key'][0],1]
            rows.append(chosen)
            for m in group:aliases[m['id']]=chosen['order_key']
        rows.sort(key=lambda m:m['order_key'])
        for m in rows:aliases[m['id']]=m['order_key']
        if before or cursor:
            pivot=aliases.get(before or cursor)
            if pivot is None:raise DomainError('NOT_FOUND','消息不属于此对话',404)
            rows=[m for m in rows if (m['order_key']<pivot if before else m['order_key']>pivot)]
        if run_id:
            run=self.repo.get('run',run_id,session['project_id'])
            if run.get('session_id')!=session_id:raise DomainError('NOT_FOUND','运行不属于此对话',404)
            rows=[m for m in rows if m.get('run_id')==run_id]
        more=len(rows)>limit
        items=rows[:limit] if cursor and not before else rows[-limit:]
        older=items[0]['id'] if more and items and not (cursor and not before) else None
        from app.services.review_feedback import ReviewFeedbackService
        items=ReviewFeedbackService(self.repo,self.settings).enrich_messages(session,items)
        output=[]
        for item in items:
            value={**item,'type':item.get('type','review_feedback' if item.get('feedback_id') else 'text')}
            if item.get('type')=='excel_confirmation':
                card=self.card(item['payload']['precheck_id'])
                if card['project_id']!=session['project_id']:raise DomainError('NOT_FOUND','确认卡不属于此项目',404)
                if card.get('session_id')!=session_id:raise DomainError('NOT_FOUND','确认卡不属于此对话',404)
                value['card']=card
            if item.get('run_id') and item.get('task_id'):
                run=self.repo.get('run',item['run_id'],session['project_id']);task=self.repo.get('task',item['task_id'],session['project_id'])
                if run['task_id']!=task['id'] or task.get('session_id')!=session_id:raise DomainError('NOT_FOUND','消息运行不属于此对话',404)
                jobs=[j for j in self.repo.list('job',session['project_id'],run['id']) if j.get('kind') in {'task','document_task'}]
                if run.get('kind')=='document_task':
                    config=self.repo.get('config_snapshot',run['config_snapshot_id'],session['project_id'])
                    value['original_text']=config.get('raw_text','')
                    value['target_language']=config['target_language']
                    if item.get('type')=='translation_result':value['result']=item.get('payload',{}).get('result',{'translation':item.get('text','')})
                else:value['original_text']=self.repo.get('input_snapshot',run['input_snapshot_id'],session['project_id']).get('raw_text','')
                value.update(mode=run['mode'],product_id=run['product_id'],job=jobs[-1] if jobs else None,is_current_run=task['active_run_id']==run['id'],current_run_id=task['active_run_id'])
                if run.get('qa_result'):value['result']=run['qa_result']
                elif run['mode']=='SOC_SINGLE':
                    binding=self.repo.list('run_binding',session['project_id'],run['id'],limit=1)
                    if binding and binding[0].get('result_id'):
                        record=self.repo.get('clause_result',binding[0]['result_id'],session['project_id'])
                        value.update(clause_id=binding[0]['clause_id'],result=record['result'],manually_revised=bool(record.get('parent_result_id')),review_status=binding[0].get('review_status','pending'))
            if value.get('result') and value.get('mode')!='TRANSLATION':
                from app.domain.response_contract import display_result
                value['result']=display_result(value['result'])
            output.append(value)
        return {'items':output,'next_cursor':(items[-1]['id'] if more and items else None) if cursor and not before else older,'older_cursor':older}
