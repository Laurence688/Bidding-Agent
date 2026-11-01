"""复制会话文字快照，保留来源信息；不搬迁任务、证据、审核和模型调用。"""
from app.domain.errors import DomainError
from app.domain.identifiers import new_id,utc_now
from app.services.context_management import ContextService
from app.services.conversations import ConversationService


def copy_session(repo,settings,session_id,expected_version,target_project_id,idempotency_key):
    body={'session_id':session_id,'expected_version':expected_version,'target_project_id':target_project_id}
    def create(_db):
        source=repo.get_session(session_id)
        if source['version']!=expected_version:
            raise DomainError('VERSION_CONFLICT','对话已变化，请刷新后再创建副本',409)
        project=repo.get('project',target_project_id)
        if project.get('evaluation_only'):
            raise DomainError('SESSION_TARGET_INVALID','不能将对话复制到评测专用项目',422)
        original=repo.get('project',source['project_id'])
        context=ContextService(repo,settings).freeze_history(source['project_id'],session_id,original['default_product_id'])
        # 一个事务内读取视图与历史，避免复制期间任务完成导致快照前后不一致。
        messages=ConversationService(repo,settings).messages(session_id,limit=100000)['items']
        history={m['id']:m for m in context['messages']}
        pair_ids={ident:new_id() for pair in context['pairs'] for ident in pair[:1]}
        pairs={ident:pair_ids[pair[0]] for pair in context['pairs'] for ident in pair}
        is_branch=target_project_id==source['project_id']
        created=repo.put('session',{'name':source['name'][:95]+(' · 分支' if is_branch else ' · 副本'),'pinned':False,
            'copied_from':{'session_id':session_id,'session_name':source['name'],'project_id':source['project_id'],
                'project_name':original['name'],'copied_at':utc_now(),'kind':'branch' if is_branch else 'project_copy'},
            'copied_message_count':len(messages)},target_project_id)
        for message in messages:
            eligible=history.get(message['id'])
            text=message.get('text','')
            result=message.get('result') or message.get('payload',{}).get('result') or {}
            if message.get('role')=='assistant' and result:
                text=result.get('answer') or result.get('translation') or ContextService.soc_text(result) or text
                sources=list(dict.fromkeys(r['source_name'] for r in result.get('evidence_refs',[]) if r.get('source_name')))
                if sources:text+='\nSources: '+' '.join(f'[{i+1}] <{name}>' for i,name in enumerate(sources))
            if message.get('type')=='progress':text=(text+'\n原任务尚未完成，请回原对话查看后续结果。').strip()
            repo.put('session_message',{'type':'history_snapshot','role':message.get('role','assistant'),'text':text,
                'mode':message.get('mode',eligible.get('mode') if eligible else None),'product_id':message.get('product_id',eligible.get('product_id') if eligible else None),
                'context_text':eligible['text'] if eligible else '', 'context_eligible':bool(eligible),
                'history_pair_id':pairs.get(message['id']),
                'inherited_from':{'message_id':message['id'],'session_id':session_id,'project_id':source['project_id'],'created_at':message['created_at']}},target_project_id,created['id'])
        repo.audit(target_project_id,'session.copy',{'source_session_id':session_id,'new_session_id':created['id'],'message_count':len(messages)})
        return created
    return repo.idempotent(target_project_id,'session.copy',idempotency_key,body,create)
