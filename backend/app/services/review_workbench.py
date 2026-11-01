"""Project-scoped current-result review queue and safe execution summaries."""
import json
from app.domain.errors import DomainError
from app.services.result_sources import result_sources
from app.services.run_reports import RunReportService
from app.domain.response_contract import display_result

SOC_MODES=('SOC_SINGLE','SOC_BATCH')

class ReviewWorkbench:
    def __init__(self,repo,settings):self.repo,self.settings=repo,settings

    def list(self,project_id,task_id=None,product_id=None,decision=None,review_status='pending',cursor=None,limit=25,run_id=None):
        self.repo.get('project',project_id)
        if decision and decision not in {'FC','PC','NC','UNKNOWN'}:raise DomainError('FILTER_INVALID','判定筛选无效',422)
        if review_status not in {'all','pending','approved','revoked'}:raise DomainError('FILTER_INVALID','审核状态筛选无效',422)
        try:offset=int(cursor or 0)
        except (ValueError,TypeError):raise DomainError('CURSOR_INVALID','分页标识无效',422)
        if offset<0 or not 1<=limit<=200:raise DomainError('CURSOR_INVALID','分页范围无效',422)
        where=["b.kind='run_binding'","b.deleted=0","b.user_id=?","b.project_id=?","r.kind='run'","r.deleted=0","t.kind='task'","t.deleted=0","json_extract(t.data,'$.mode') IN ('SOC_SINGLE','SOC_BATCH')"]
        args=[self.repo.local_user_id,project_id]
        if run_id:
            self.repo.get('run',run_id,project_id);where.append('r.id=?');args.append(run_id)
        else:where.append("json_extract(t.data,'$.active_run_id')=r.id")
        for value,field in [(task_id,'t.id'),(product_id,"json_extract(r.data,'$.product_id')"),(decision,"json_extract(c.data,'$.result.decision')")]:
            if value:where.append(field+'=?');args.append(value)
        if review_status=='pending':where.append("COALESCE(json_extract(b.data,'$.review_status'),'pending')!='approved'")
        elif review_status!='all':where.append("json_extract(b.data,'$.review_status')=?");args.append(review_status)
        joins=""" FROM records b JOIN records r ON r.id=b.parent_id AND r.user_id=b.user_id AND r.project_id=b.project_id
          JOIN records t ON t.id=json_extract(r.data,'$.task_id') AND t.user_id=b.user_id AND t.project_id=b.project_id
          LEFT JOIN records c ON c.id=json_extract(b.data,'$.result_id') AND c.user_id=b.user_id AND c.project_id=b.project_id AND c.deleted=0
          WHERE """+' AND '.join(where)
        with self.repo.connection() as db:
            total=db.execute('SELECT COUNT(*)'+joins,args).fetchone()[0]
            records=db.execute("SELECT b.*,c.data AS result_data,r.id AS run_id,r.data AS run_data,t.id AS task_id,t.data AS task_data"+joins+' ORDER BY b.created_at DESC,b.id DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        items=[]
        for record in records:
            row=self.repo.decode(record);run=json.loads(record['run_data']);task=json.loads(record['task_data']);result_record=json.loads(record['result_data']) if record['result_data'] else {}
            items.append({**row,'run_id':record['run_id'],'task_id':record['task_id'],'product_id':run.get('product_id'),'mode':task['mode'],
              'historical':task['active_run_id']!=record['run_id'],'result':display_result(result_record.get('result')),'manually_revised':bool(result_record.get('parent_result_id'))})
        return {'items':items,'total':total,'next_cursor':str(offset+limit) if offset+limit<total else None,'scope':'current_soc_result'}

    def detail(self,run_id,clause_id):
        run=self.repo.get('run',run_id);task=self.repo.get('task',run['task_id'],run['project_id'])
        if task['mode'] not in SOC_MODES:raise DomainError('MODE_INVALID','应答审核仅适用于 SOC 任务',422)
        bindings=[b for b in self.repo.list('run_binding',run['project_id'],run_id,limit=100000) if b['clause_id']==clause_id]
        if not bindings:raise DomainError('NOT_FOUND','条目不属于该运行',404)
        row=bindings[0];result=self.repo.get('clause_result',row['result_id'],run['project_id']) if row.get('result_id') else None
        revisions=[];current=result
        while current:
            revisions.append({'id':current['id'],'created_at':current['created_at'],'result':current['result']})
            current=self.repo.get('clause_result',current['parent_result_id'],run['project_id']) if current.get('parent_result_id') else None
        annotations=[a for a in self.repo.list('clause_annotation',run['project_id'],row['id'],limit=100000) if a.get('run_id')==run_id]
        annotation=max(annotations,key=lambda a:a['revision']) if annotations else None
        return {**row,'annotation':annotation,'annotation_stale':bool(annotation and annotation['result_id']!=row.get('result_id')),'run_id':run_id,'task_id':task['id'],'product_id':run['product_id'],'historical':task['active_run_id']!=run_id,
          'result':display_result(result['result']) if result else None,'sources':result_sources(result['result'] if result else None),
          'comments':self.repo.list('review_comment',run['project_id'],row['id']),
          'reviews':self.repo.list('review',run['project_id'],row['id']), 'revisions':revisions,
          'manually_revised':bool(result and result.get('parent_result_id'))}

    def scope_sources(self,run_id):
        run=self.repo.get('run',run_id)
        if not run.get('knowledge_snapshot_id'):return []
        snapshot=self.repo.get('knowledge_snapshot',run['knowledge_snapshot_id'])
        fields=('source_name','version_label','document_type','product_id','status')
        return [{**{k:source.get(k) for k in fields},'source_version_id':source['id']} for source in (self.repo.get('source_version',ident) for ident in snapshot.get('source_version_ids',[]))]

    def execution(self,run_id,clause_id=None):
        run=self.repo.get('run',run_id);task=self.repo.get('task',run['task_id'],run['project_id'])
        attempts=RunReportService(self.repo,self.settings).attempts(run_id)
        if clause_id:attempts=[a for a in attempts if a.get('clause_id')==clause_id]
        fields=('attempt_id','logical_node_name','attempt_index','provider','model','status','latency','error_code','started_at','ended_at','usage')
        calls=[{k:a.get(k) for k in fields} for a in attempts]
        jobs=[j for j in self.repo.list('job',run['project_id'],run_id,limit=100000) if j.get('run_id')==run_id]
        safe_jobs=[{k:j.get(k) for k in ('id','kind','status','stage','current_node','processed_items','total_items','cancel_requested','version')} for j in jobs]
        with self.repo.connection() as db:
            events=db.execute('''SELECT e.event_id,e.type,e.data,e.created_at FROM job_events e JOIN records j ON j.id=e.job_id
              WHERE j.user_id=? AND j.project_id=? AND j.parent_id=? ORDER BY e.event_id DESC LIMIT 100''',(self.repo.local_user_id,run['project_id'],run_id)).fetchall()
        with self.repo.connection() as db:
            node_rows=db.execute('''SELECT c.node_name,c.clause_id,c.created_at,t.elapsed_seconds FROM node_commits c LEFT JOIN node_timings t USING(run_id,clause_id,node_name,input_hash) WHERE c.run_id=? AND (? IS NULL OR c.clause_id=?) ORDER BY c.created_at''',(run_id,clause_id,clause_id)).fetchall()
        nodes=[{**dict(n),'status':'completed'} for n in node_rows]
        allowed={'action','error_code','stage','status','current_node','processed_items','total_items','code','elapsed_seconds','cancel_requested','clause_id'}
        return {'run_id':run_id,'task_id':task['id'],'mode':task['mode'],'jobs':safe_jobs,'attempts':calls,'nodes':nodes,
          'events':[{**{k:e[k] for k in ('event_id','type','created_at')},'data':{k:v for k,v in json.loads(e['data']).items() if k in allowed}} for e in events],
          'note':'仅展示实际节点、状态和用量，不提供内部推理或原始提示词。'}

    def logs(self,project_id,mode=None,cursor=None,limit=25,category=None):
        self.repo.get('project',project_id)
        try:offset=int(cursor or 0)
        except (ValueError,TypeError):raise DomainError('CURSOR_INVALID','分页标识无效',422)
        if offset<0:raise DomainError('CURSOR_INVALID','分页标识无效',422)
        args=[self.repo.local_user_id,project_id];where="r.kind='run' AND r.user_id=? AND r.project_id=? AND r.deleted=0 AND t.deleted=0"
        if mode:where+=" AND json_extract(r.data,'$.mode')=?";args.append(mode)
        if category and category not in {'TRANSLATION_TEXT','TRANSLATION_FILE','TRANSLATION_UNKNOWN'}:raise DomainError('FILTER_INVALID','分类筛选无效',422)
        # Infer only from frozen input evidence, never from segment count. Unknown stays unknown.
        category_sql="""CASE WHEN json_extract(r.data,'$.mode')!='TRANSLATION' THEN json_extract(r.data,'$.mode')
          WHEN json_extract(c.data,'$.input_kind')='file' THEN 'TRANSLATION_FILE'
          WHEN json_extract(c.data,'$.input_kind')='text' THEN 'TRANSLATION_TEXT'
          WHEN json_type(c.data,'$.files')='array' AND EXISTS(SELECT 1 FROM json_each(c.data,'$.files') f WHERE json_type(f.value,'$.id')='text' AND json_extract(f.value,'$.id')!='text') THEN 'TRANSLATION_FILE'
          WHEN json_type(c.data,'$.files')='array' AND ((json_array_length(c.data,'$.files')>0 AND NOT EXISTS(SELECT 1 FROM json_each(c.data,'$.files') f WHERE json_extract(f.value,'$.id') IS NULL OR json_extract(f.value,'$.id')!='text')) OR length(trim(COALESCE(NULLIF(json_extract(c.data,'$.raw_text'),''),json_extract(c.data,'$.resolved_source'),'')))>0) THEN 'TRANSLATION_TEXT'
          ELSE 'TRANSLATION_UNKNOWN' END"""
        if category:where+=' AND ('+category_sql+')=?';args.append(category)
        joins=" FROM records r JOIN records t ON t.id=json_extract(r.data,'$.task_id') AND t.user_id=r.user_id AND t.project_id=r.project_id LEFT JOIN records c ON c.id=json_extract(r.data,'$.config_snapshot_id') AND c.user_id=r.user_id AND c.project_id=r.project_id AND c.deleted=0 WHERE "+where
        with self.repo.connection() as db:
            total=db.execute('SELECT COUNT(*)'+joins,args).fetchone()[0]
            runs=db.execute('SELECT r.*,('+category_sql+') AS display_category'+joins+' ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        from app.services.task_labels import task_labels
        labels=task_labels(self.repo,project_id)
        tasks={t['id']:t for t in self.repo.list('task',project_id,limit=100000)}
        rows=[]
        for record in runs:
            run=self.repo.decode(record);jobs=self.repo.list('job',project_id,run['id'],limit=100000);job=next((j for j in jobs if j.get('kind') in {'task','document_task'}),None)
            rows.append({**labels(tasks.get(run['task_id'],{})),'run_id':run['id'],'task_id':run['task_id'],'mode':run['mode'],'display_category':record['display_category'],'product_id':run.get('product_id'),'created_at':run['created_at'],'job':{k:job.get(k) for k in ('id','status','processed_items','total_items','current_node')} if job else None})
        return {'items':rows,'total':total,'next_cursor':str(offset+limit) if offset+limit<total else None}
