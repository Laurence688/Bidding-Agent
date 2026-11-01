"""Read-only provenance views plus independent manual memory enablement.

Nothing in this service supplies memory to retrieval or model prompts.
"""
from app.domain.errors import DomainError
from app.domain.export_gate import validation_digest, result_blockers
from app.schemas.core import ClauseResult
from app.services.knowledge import KnowledgeService

class MemoryManagementService:
    def __init__(self,repo,settings):
        self.repo,self.settings=repo,settings

    def _view(self,memory):
        project=memory['project_id'];applicability=memory.get('applicability',{})
        result=None;run_id=None;task_id=None;state='source_needs_review'
        try:
            record=self.repo.get('clause_result',memory['result_id'],project)
            result=record['result'];review=self.repo.get('review',memory['review_id'],project)
            run_id=review['run_id'];run=self.repo.get('run',run_id,project);task_id=run['task_id']
            task=self.repo.get('task',task_id,project)
            binding=self.repo.get('run_binding',review['parent_id'],project)
            if binding.get('result_id')!=record['id']:
                state='result_replaced'
            elif binding.get('review_status')!='approved' or binding.get('approved_review_id')!=review['id'] or review.get('status')!='approved':
                state='approval_revoked'
            else:
                if task['active_run_id']!=run_id:
                    inherited=[b for b in self.repo.list('run_binding',project,task['active_run_id'],limit=100000)
                        if b.get('result_id')==record['id'] and b.get('approved_review_id')==review['id'] and b.get('review_status')=='approved']
                    if not inherited:return self._payload(memory,result,run_id,task_id,'result_replaced')
                sources=sorted({ref['source_version_id'] for a in result.get('assessments',[]) for ref in a.get('evidence_refs',[])})
                states=[{'id':ident,'version':self.repo.get('source_version',ident)['version'],'status':self.repo.get('source_version',ident)['status']} for ident in sources]
                valid=review['result_id']==record['id'] and review['result_version']==record['version'] and review['validation_hash']==validation_digest(result,states)
                products=self.repo.get('config_snapshot',run['config_snapshot_id'])['products']
                blockers=result_blockers(ClauseResult.model_validate(result),run['product_id'],products,KnowledgeService.from_settings(self.repo,self.settings))
                state='approval_valid' if valid and not blockers else 'source_needs_review'
        except (DomainError,KeyError,ValueError):
            state='source_needs_review'
        return self._payload(memory,result,run_id,task_id,state)

    def _payload(self,memory,result,run_id,task_id,state):
        applicability=memory.get('applicability',{})
        return {**memory,'manual_disabled':bool(memory.get('manual_disabled',False)),
            'management_status':'disabled' if memory.get('manual_disabled',False) else 'enabled',
            'source_status':state,'raw_text':(result or {}).get('raw_text',applicability.get('raw_text','')),
            'response_en':(result or {}).get('response_en',applicability.get('response_en')),
            'product_id':applicability.get('product_id'),'run_id':run_id,'task_id':task_id,'result':result,
            'authoritative':False}

    def list(self,project_id,q=''):
        self.repo.get('project',project_id)
        items=[self._view(m) for m in self.repo.list('project_memory',project_id,limit=100000)]
        query=q.casefold().strip()
        return {'items':[m for m in reversed(items) if query in (m['raw_text']+' '+(m['response_en'] or '')+' '+(m['product_id'] or '')).casefold()]}

    def get(self,project_id,memory_id):
        self.repo.get('project',project_id)
        value=self._view(self.repo.get('project_memory',memory_id,project_id))
        review=self.repo.get('review',value['review_id'],project_id)
        sources=sorted({ref['source_version_id'] for assessment in (value.get('result') or {}).get('assessments',[]) for ref in assessment.get('evidence_refs',[])})
        history=[]
        for ident in sources:
            try:
                source=self.repo.get('source_version',ident)
                history.append({key:source.get(key) for key in ['id','source_name','version','version_label','document_id','library_id','status','created_at','file_sha256','parser_fingerprint']})
            except DomainError as exc:
                if exc.code!='NOT_FOUND':raise
                history.append({'id':ident,'status':'unavailable','source_name':'历史来源不可用'})
        review_history=[{key:item.get(key) for key in ['id','version','status','result_id','result_version','run_id','created_at','comments']} for item in self.repo.list('review',project_id,review['parent_id'],limit=100000)]
        return {**value,'review_version':{key:review.get(key) for key in ['id','version','status','result_id','result_version','run_id','created_at']},'source_history':history,'review_history':review_history}

    def set_enabled(self,project_id,memory_id,expected_version,enabled):
        with self.repo.transaction():
            self.repo.get('project_memory',memory_id,project_id)
            updated=self.repo.update('project_memory',memory_id,{'manual_disabled':not enabled},expected_version,project_id)
            self.repo.audit(project_id,'memory.enable' if enabled else 'memory.disable',{'memory_id':memory_id})
            return self._view(updated)
