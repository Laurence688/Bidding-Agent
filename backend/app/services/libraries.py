"""Versioned product libraries. Preview is pure parsing; only an explicit build embeds."""
from pathlib import Path
import re, json
from app.domain.errors import DomainError
from app.domain.identifiers import new_id, file_hash, content_hash, utc_now
from app.services.knowledge import KnowledgeService
from app.security.local_access import controlled_path

from app.services.product_catalog import ProductCatalogService
from app.services.library_wizard import LibraryWizardMixin
from app.services.library_purge import LibraryPurgeMixin

class LibraryService(ProductCatalogService,LibraryWizardMixin,LibraryPurgeMixin):
    def __init__(self,repo,settings):self.repo,self.settings=repo,settings
    def legacy(self,ident):
        product=ident.removeprefix('legacy:');self.resolve_product(product)
        if product not in {p['id'] for p in self.settings.products}:raise DomainError('NOT_FOUND','知识库不存在',404)
        heads=[h for h in self.repo.list('knowledge_head') if h['product_id']==product]
        active=self.repo.get('knowledge_snapshot',heads[0]['snapshot_id']) if heads else None
        try:state=self.repo.get('kb_library_state','state:'+ident)
        except DomainError as e:
            if e.code!='NOT_FOUND':raise
            state={'enabled':True,'trashed':False,'version':1}
        return {**state,'id':ident,'name':product+' 既有产品库','product_id':product,'readonly':True,'active_snapshot_id':active['id'] if active else None}
    def library(self,ident):return self.legacy(ident) if ident.startswith('legacy:') else self.repo.get('kb_library',ident)
    def libraries(self):
        result=[]
        for lib in [self.legacy('legacy:'+p['id']) for p in self.settings.products]+self.repo.list('kb_library'):
            versions=[s for s in self.repo.list('knowledge_snapshot',parent_id=lib['id']) if s.get('status')=='ready']
            docs=self.repo.list('kb_document',parent_id=lib['id'])
            if lib.get('readonly'):
                versions=[s for s in self.repo.list('knowledge_snapshot') if s['product_id']==lib['product_id'] and s.get('status')=='ready' and not s.get('library_id') and not s.get('child_snapshot_ids')]
                active=self.repo.get('knowledge_snapshot',lib['active_snapshot_id']) if lib.get('active_snapshot_id') else None
                sources=[self.repo.get('source_version',i) for i in (active or {}).get('source_version_ids',[])]
                count=sum(s.get('chunk_count',0) for s in sources);document_count=len(sources)
            else:
                document_count=len(docs);count=0
                for doc in docs:
                    previews=self.repo.list('kb_preview',parent_id=doc['id'])
                    if previews:count+=previews[-1].get('chunk_count',0)
            published=self.published_view(lib)
            result.append({**lib,'draft_document_count':len([d for d in docs if not d.get('draft_discarded')]),'draft_chunk_count':count,'published_document_count':published['document_count'],'published_chunk_count':published['chunk_count'],'ready_to_publish':any(b['status']=='ready' and not b.get('published_at') and not b.get('draft_abandoned') for b in self.repo.list('kb_build',parent_id=lib['id'])),'trashed':bool(lib.get('trashed',False)),'document_count':published['document_count'] if lib.get('active_snapshot_id') else document_count,'chunk_count':published['chunk_count'] if lib.get('active_snapshot_id') else count,'snapshot_count':len(versions)})
        # A committed purge whose file cleanup failed remains discoverable after
        # closing the dialog or restarting. It can only resume the confirmed purge.
        with self.repo.connection() as db:
            pending=[self.repo.decode(row) for row in db.execute("SELECT * FROM records WHERE kind='library_purge' AND user_id=? AND deleted=0 AND json_extract(data,'$.status')='cleanup_pending'",(self.repo.local_user_id,))]
        for receipt in pending:
            result.append({'id':receipt['library_id'],'name':receipt['name'],'product_id':receipt.get('product_id'),'version':receipt['expected_version'],'trashed':True,'enabled':False,'purge_cleanup_pending':True,'document_count':0,'chunk_count':0,'snapshot_count':0})
        return result
    def published_view(self,lib):
        snapshot=self.repo.get('knowledge_snapshot',lib['active_snapshot_id']) if lib.get('active_snapshot_id') else None
        sources=[self.repo.get('source_version',i) for i in (snapshot or {}).get('source_version_ids',[])]
        return {'snapshot':snapshot,'documents':sources,'document_count':len(sources),'chunk_count':sum(v.get('chunk_count',0) for v in sources),'embedding_model':(snapshot or {}).get('embedding_profile',self.default_profile()).get('model')}
    def library_projects(self,ident):
        with self.repo.transaction():
            self.library(ident)
            projects=self.repo.list('project',limit=100000)
            rows=[(p,self.bindings(p['id'])) for p in projects]
            token=content_hash([(p['id'],b['version'],b['library_ids']) for p,b in rows])
            return {'expected_version':token,'project_ids':[p['id'] for p,b in rows if ident in b['library_ids']],'projects':projects}
    def bind_library_projects(self,ident,ids,expected_version):
        if len(ids)!=len(set(ids)):raise DomainError('BINDING_INVALID','项目不能重复',422)
        with self.repo.transaction():
            lib=self.library(ident);self._available(lib)
            current=self.library_projects(ident)
            if current['expected_version']!=expected_version:raise DomainError('VERSION_CONFLICT','项目绑定已变化，请刷新后重新保存',409)
            if set(ids)-set(current['project_ids']):self._available(lib,enabled=True)
            if set(ids)-{p['id'] for p in current['projects']}:raise DomainError('NOT_FOUND','项目不存在',404)
            for project in current['projects']:
                binding=self.bindings(project['id']);old=binding['library_ids']
                new=[v for v in old if v!=ident]+([ident] if project['id'] in ids else [])
                if set(new)==set(old):continue
                if binding['version']:self.repo.update('kb_binding','kb-binding:'+project['id'],{'library_ids':new},binding['version'])
                else:self.repo.put('kb_binding',{'library_ids':new},project['id'],record_id='kb-binding:'+project['id'])
            return self.library_projects(ident)
    def discard_draft(self,ident,expected_version):
        with self.repo.transaction():
            lib=self.library(ident)
            if lib['version']!=expected_version:raise DomainError('VERSION_CONFLICT','草稿已变化，请刷新',409)
            if not lib.get('active_snapshot_id'):return self.trash(ident,expected_version)
            if not lib.get('has_draft'):return lib
            for build in self.repo.list('kb_build',parent_id=ident):
                if not build.get('published_at'):self.repo.update('kb_build',build['id'],{'draft_abandoned':True})
            baseline=lib.get('draft_baseline') or {}
            for doc in self.repo.list('kb_document',parent_id=ident):
                previous=baseline.get(doc['id'])
                self.repo.update('kb_document',doc['id'],previous if previous else {'draft_discarded':True})
            return self._update_library(ident,{'wizard_draft':{},'has_draft':False,'draft_baseline':None},expected_version)
    def start_draft(self,ident):
        lib=self.library(ident)
        if lib.get('readonly'):raise DomainError('READ_ONLY','既有库只读；请创建可维护的新库',409)
        if not lib.get('has_draft'):
            baseline={d['id']:{'enabled':d.get('enabled',True),'rule_draft':d.get('rule_draft'),'draft_discarded':d.get('draft_discarded',False)} for d in self.repo.list('kb_document',parent_id=ident)}
            return self._update_library(ident,{'has_draft':True,'draft_baseline':baseline},lib['version'])
        return lib
    def create_library(self,body):
        name=str(body.get('name','')).strip();product=self.resolve_product(body.get('product_id'))
        if not name or len(name)>100:raise DomainError('LIBRARY_INVALID','请输入1至100字符库名称',422)
        return self.repo.put('kb_library',{'name':name,'product_id':product,'enabled':True,'active_snapshot_id':None,'has_draft':True,'draft_baseline':{},'description':body.get('description',''),'wizard_draft':body.get('wizard_draft',{})})
    def update(self,kind,ident,version,enabled):
        if not isinstance(enabled,bool):raise DomainError('VALIDATION_ERROR','enabled必须为布尔值',422)
        if kind=='kb_library':
            with self.repo.transaction():
                lib=self.library(ident);self._available(lib)
                value=self._update_library(ident,{'enabled':enabled},version)
                self.repo.audit(None,'library.enable' if enabled else 'library.disable',{'library_id':ident,'previous_version':version,'version':value['version']})
                return value
        with self.repo.transaction():
            doc=self.repo.get('kb_document',ident);self._available(self.library(doc['library_id']))
            value=self.repo.update(kind,ident,{'enabled':enabled},version)
            self.repo.audit(None,'library.document.enable' if enabled else 'library.document.disable',{'document_id':ident,'library_id':doc['library_id'],'previous_version':version,'version':value['version']})
            return value
    def _available(self,lib,enabled=False):
        if lib.get('trashed'):raise DomainError('LIBRARY_TRASHED','知识库已移至回收站，请恢复并重新确认项目绑定',409)
        if enabled and not lib.get('enabled'):raise DomainError('LIBRARY_DISABLED','知识库已停用',409)
    def _update_library(self,ident,changes,expected_version):
        if not ident.startswith('legacy:'):return self.repo.update('kb_library',ident,changes,expected_version)
        current=self.library(ident)
        if current['version']!=expected_version:raise DomainError('VERSION_CONFLICT','内容已更新，请刷新后重新操作',409)
        try:self.repo.get('kb_library_state','state:'+ident)
        except DomainError as e:
            if e.code!='NOT_FOUND':raise
            self.repo.put('kb_library_state',{'enabled':current['enabled'],'trashed':False},record_id='state:'+ident)
        self.repo.update('kb_library_state','state:'+ident,changes,expected_version)
        return self.library(ident)
    def check_legacy(self,product_id,job_id=None):
        self._available(self.library('legacy:'+product_id),enabled=True)
        if job_id:
            job=self.repo.get('job',job_id)
            if job.get('cancel_requested') or job.get('status') in {'cancelled','cancelling'}:
                raise DomainError('BUILD_CANCELLED','构建已取消，未发布新版本',409)
    def enqueue_legacy(self,product_id):
        with self.repo.transaction():
            self.check_legacy(product_id)
            return self.repo.put('job',{'kind':'knowledge','product_id':product_id,'status':'queued','cancel_requested':False})
    def trash(self,ident,expected_version):
        from app.domain.identifiers import utc_now
        with self.repo.transaction():
            lib=self.library(ident)
            if lib['version']!=expected_version:raise DomainError('VERSION_CONFLICT','内容已更新，请刷新后重新操作',409)
            self._available(lib)
            for build in self.repo.list('kb_build',parent_id=ident,limit=100000):
                job=self.repo.get('job',build['job_id']) if build.get('job_id') else None
                if (job and job['status'] in {'queued','running','cancelling'}) or (not job and build['status'] in {'queued','running'}):
                    raise DomainError('BUILD_STOP_REQUIRED','请先取消构建，等待 Worker 确认停止后再移至回收站',409)
            if ident.startswith('legacy:') and any(j.get('kind')=='knowledge' and j.get('product_id')==lib['product_id'] and j.get('status') in {'queued','running','cancelling'} for j in self.repo.list('job',limit=100000)):
                raise DomainError('BUILD_STOP_REQUIRED','请先取消构建，等待 Worker 确认停止后再移至回收站',409)
            # Materialize compatibility defaults before removing this library so restore never rebinds.
            for project in self.repo.list('project',limit=100000):
                binding=self.bindings(project['id'])
                if ident in binding['library_ids']:
                    remaining=[i for i in binding['library_ids'] if i!=ident]
                    if binding['version']:self.repo.update('kb_binding','kb-binding:'+project['id'],{'library_ids':remaining})
                    else:self.repo.put('kb_binding',{'library_ids':remaining},project['id'],record_id='kb-binding:'+project['id'])
            value=self._update_library(ident,{'trashed':True,'trashed_at':utc_now(),'enabled_before_trash':lib.get('enabled',True),'enabled':False,'requires_binding_confirmation':True},expected_version)
            self.repo.audit(None,'library.trash',{'library_id':ident,'previous_version':expected_version,'version':value['version']})
            return value
    def restore(self,ident,expected_version):
        with self.repo.transaction():
            lib=self.library(ident)
            if lib['version']!=expected_version:raise DomainError('VERSION_CONFLICT','内容已更新，请刷新后重新操作',409)
            if not lib.get('trashed'):raise DomainError('LIBRARY_NOT_TRASHED','知识库不在回收站',409)
            self._recover_uncommitted_purge(ident)
            value=self._update_library(ident,{'trashed':False,'trashed_at':None,'enabled':lib.get('enabled_before_trash',True),'requires_binding_confirmation':True},expected_version)
            self.repo.audit(None,'library.restore',{'library_id':ident,'previous_version':expected_version,'version':value['version'],'binding_restored':False})
            return value
    def cancel_build(self,ident,expected_version):
        with self.repo.transaction():
            build=self.repo.get('kb_build',ident)
            if build['version']!=expected_version:raise DomainError('VERSION_CONFLICT','构建状态已变化，请刷新',409)
            if build['status'] not in {'queued','running'}:raise DomainError('BUILD_NOT_RUNNING','构建已停止',409)
            job=self.repo.get('job',build['job_id'])
            immediately_stopped=job['status'] in {'queued','interrupted'}
            self.repo.update('job',build['job_id'],{'cancel_requested':True,**({'status':'cancelled'} if immediately_stopped else {})})
            if immediately_stopped:self.repo.update('kb_build',ident,{'status':'cancelled','finished_at':utc_now()})
            self.repo.event(job['id'],'job.status',{'status':'cancelled' if immediately_stopped else job['status'],'cancel_requested':True})
            self.repo.audit(None,'library.build.cancel_requested',{'library_id':build['library_id'],'build_id':ident})
            return self.build_view(build)
    def _check_build(self,ident):
        build=self.repo.get('kb_build',ident);job=self.repo.get('job',build['job_id']) if build.get('job_id') else {}
        if build['status']=='cancelled' or job.get('cancel_requested') or job.get('status')=='cancelled':raise DomainError('BUILD_CANCELLED','构建已取消，未发布新版本',409)
        self._available(self.library(build['library_id']))
    def detail(self,ident):
        lib=self.library(ident)
        if lib.get('readonly'):
            snapshot=KnowledgeService.from_settings(self.repo,self.settings).active(lib['product_id'])
            return {'library':lib,'published':self.published_view(lib),'documents':self.published_view(lib)['documents'],'builds':[],'versions':[snapshot] if snapshot else [],'embedding_profiles':self.profiles()}
        docs=[]
        for doc in self.repo.list('kb_document',parent_id=ident):
            if doc.get('draft_discarded'):continue
            previews=self.repo.list('kb_preview',parent_id=doc['id'])
            docs.append({**doc,'latest_preview':previews[-1] if previews else None})
        return {'library':lib,'published':self.published_view(lib),'documents':docs,'builds':[self.build_view(b) for b in reversed(self.repo.list('kb_build',parent_id=ident))],'versions':self.repo.list('knowledge_snapshot',parent_id=ident),'embedding_profiles':self.profiles()}
    def build_view(self,build):
        value=dict(build);job={};events=[]
        if build.get('job_id'):
            job=self.repo.get('job',build['job_id'])
            value['cancel_requested']=bool(job.get('cancel_requested'))
            if job['status']=='running' and build['status'] in {'queued','running'}:
                from app.worker.leases import owner_alive
                if not owner_alive(job.get('owner_pid'),job.get('owner_started')):value.update(status='interrupted',error={'code':'WORKER_INTERRUPTED','message':'执行进程已停止，请重新构建；未发布新版本'})
            if job['status'] in {'failed','interrupted','cancelled'} and build['status'] in {'queued','running'}:value.update(status=job['status'],error=job.get('error'))
            with self.repo.connection() as db:
                events=[dict(row) for row in db.execute('SELECT type,data,created_at FROM job_events WHERE job_id=? ORDER BY event_id',(job['id'],))]
        value['submitted_at']=build.get('created_at')
        value['started_at']=value.get('started_at') or next((e['created_at'] for e in events if json.loads(e['data']).get('status')=='running'),None)
        value['finished_at']=value.get('finished_at') or next((e['created_at'] for e in reversed(events) if json.loads(e['data']).get('status') in {'completed','succeeded','failed','cancelled','interrupted'}),None)
        value.setdefault('stage','completed' if value['status']=='ready' else ('queued' if value['status']=='queued' else None))
        value['progress']=None
        value['publication_status']='published' if (value.get('published_at') or (value.get('snapshot_id') and self.library(value['library_id']).get('active_snapshot_id')==value['snapshot_id'])) else 'pending'
        if value.get('started_at') and (value.get('finished_at') or value['status']=='running'):
            from datetime import datetime,timezone
            try:value['duration_seconds']=max(0,((datetime.fromisoformat(value['finished_at']) if value.get('finished_at') else datetime.now(timezone.utc))-datetime.fromisoformat(value['started_at'])).total_seconds())
            except (ValueError,TypeError):value['duration_seconds']=None
        else:value['duration_seconds']=None
        if value.get('embedding_profile'):value['embedding_profile']=self.public_profile(value['embedding_profile'])
        return value
    def bindings(self,project_id):
        self.repo.get('project',project_id)
        try:
            binding=self.repo.get('kb_binding','kb-binding:'+project_id,project_id)
            unavailable=[]
            for ident in binding['library_ids']:
                lib=self.library(ident)
                if not lib.get('enabled') or lib.get('trashed'):unavailable.append(ident)
            return {**binding,'unavailable_library_ids':unavailable}
        except DomainError as e:
            if e.code!='NOT_FOUND':raise
            return {'project_id':project_id,'library_ids':[l['id'] for l in [self.legacy('legacy:'+p['id']) for p in self.settings.products] if l.get('enabled') and not l.get('trashed') and not l.get('requires_binding_confirmation')],'version':0,'compatibility_default':True}
    def bind(self,project_id,ids,expected_version=None):
        if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):raise DomainError('BINDING_INVALID','知识库绑定列表格式不正确',422)
        with self.repo.transaction():
            current=self.bindings(project_id)
            for ident in ids:self._available(self.library(ident),enabled=ident not in current['library_ids'])
            if expected_version is not None and current['version']!=expected_version:raise DomainError('VERSION_CONFLICT','项目绑定已更新，请刷新',409)
            if current['version']:return self.repo.update('kb_binding','kb-binding:'+project_id,{'library_ids':ids})
            return self.repo.put('kb_binding',{'library_ids':ids},project_id,record_id='kb-binding:'+project_id)
    def upload(self,library_id,name,content,document_type='product_description'):
        lib=self.library(library_id)
        self._available(lib)
        if lib.get('readonly'):raise DomainError('READ_ONLY','请新建产品库后上传',409)
        if document_type not in {'product_description','feature_list','standard_list','roadmap','other'}:raise DomainError('SOURCE_TYPE_INVALID','来源类型无效',422)
        if re.search(r'roadmap|规划|路线图',name,re.I):document_type='roadmap'
        suffix=Path(name).suffix.lower()
        if suffix not in {'.docx','.xlsx','.pdf'}:raise DomainError('FILE_TYPE','仅支持DOCX、XLSX和文字PDF',415)
        if not content or len(content)>50*1024*1024:raise DomainError('FILE_SIZE','文件为空或超过50MiB',413)
        self.start_draft(library_id)
        ident=new_id();target=controlled_path(self.repo.data_dir,f'library-files/{ident}{suffix}');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        return self.repo.put('kb_document',{'library_id':library_id,'product_id':lib['product_id'],'name':Path(name).name,'document_type':document_type,'managed_path':str(target.relative_to(self.repo.data_dir)),'file_sha256':file_hash(target),'enabled':True},parent_id=library_id,record_id=ident)
    def preview(self,document_id,body):
        doc=self.repo.get('kb_document',document_id);path=controlled_path(self.repo.data_dir,doc['managed_path'])
        self._available(self.library(doc['library_id']))
        if file_hash(path)!=doc['file_sha256']:raise DomainError('FILE_CHANGED','受控文档已变化，请重新上传',409)
        from app.rag.chunking import docx_sections,split_blocks,SPLIT_PARSER_VERSION,WHOLE_DOCUMENT_PARSER_VERSION
        config=self.split_config(body);strategy=config['strategy'];warnings=[]
        # Templates are copied rules: later edits/deletion cannot change this config.
        if strategy=='whole_document' and path.suffix.lower() not in {'.docx','.pdf'}:
            raise DomainError('SPLIT_STRATEGY','保留原始文档仅适用于 DOCX 和文字 PDF；Excel 请使用按记录策略',422)
        if path.suffix.lower()=='.xlsx':
            if strategy not in {'excel','excel_records'}:raise DomainError('SPLIT_STRATEGY','Excel需选择工作表、列和范围',422)
            blocks=self._excel(path,config.get('selection') or {})
        elif strategy=='leaf_sections' or strategy=='whole_document' and path.suffix.lower()=='.docx':
            if path.suffix.lower()!='.docx':raise DomainError('SPLIT_STRATEGY','完整叶标题小节适用于Word，请为PDF选择段落或固定长度',422)
            try:blocks,warnings=docx_sections(path,whole_document=strategy=='whole_document')
            except Exception as exc:
                raise DomainError('SOURCE_PARSE_FAILED','来源解析失败，原件已保留',422,{'type':type(exc).__name__}) from exc
        else:
            if strategy in {'excel','excel_records'}:raise DomainError('SPLIT_STRATEGY','请选择文本切分策略',422)
            from app.rag.parsers import parse_document
            blocks=parse_document(path)
        chunks=split_blocks(blocks,config)
        if not chunks:raise DomainError('SOURCE_EMPTY','所选范围没有可提取内容',422)
        self.validate_preview_budget({'chunks':chunks},self.profile(config['embedding_profile_id']))
        record={'document_id':document_id,'library_id':doc['library_id'],'product_id':doc['product_id'],'file_sha256':doc['file_sha256'],**config,'effective_config':config,'parser_version':WHOLE_DOCUMENT_PARSER_VERSION if strategy=='whole_document' else SPLIT_PARSER_VERSION,'warnings':warnings,'chunks':chunks,'chunk_count':len(chunks),'content_hash':content_hash(chunks)}
        with self.repo.transaction():
            preview=self.repo.put('kb_preview',record,parent_id=document_id)
            if strategy not in {'default','excel'} or doc.get('rule_draft') is not None:self.repo.update('kb_document',document_id,{'rule_draft':config,'valid_preview_id':preview['id']})
            return preview
    def document_metadata(self,document_id,header_row=1):
        doc=self.repo.get('kb_document',document_id);path=controlled_path(self.repo.data_dir,doc['managed_path'])
        if file_hash(path)!=doc['file_sha256']:raise DomainError('FILE_CHANGED','受控文档已变化，请重新上传',409)
        if path.suffix.lower()!='.xlsx':return {'document_id':document_id,'kind':path.suffix.lstrip('.'),'sheets':[]}
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        if not isinstance(header_row,int) or isinstance(header_row,bool) or not 1<=header_row<=10000:raise DomainError('EXCEL_SELECTION_INVALID','表头行无效',422)
        try:book=load_workbook(path,data_only=False,read_only=True)
        except Exception as exc:raise DomainError('EXCEL_PARSE_INVALID','无法解析 Excel，请提供有效的 XLSX 文件',422) from exc
        try:
            sheets=[]
            for sheet in book:
                if sheet.max_row is None or sheet.max_column is None:sheet.calculate_dimension(force=True)
                headers=[{'column':get_column_letter(i),'label':str(sheet.cell(header_row,i).value or get_column_letter(i))} for i in range(1,sheet.max_column+1)]
                parameters=[h['column'] for h in headers if re.search(r'参数|单位|容量|带宽|吞吐|速率|数量|范围|单位|capacity|unit|gbps|mbps|range|maximum|minimum|limit',h['label'],re.I)]
                content=[h['column'] for h in headers if h['column'] not in parameters]
                sheets.append({'name':sheet.title,'rows':sheet.max_row,'columns':sheet.max_column,'header_row':header_row,'headers':headers,'candidates':{'content_columns':content,'parameter_columns':parameters},'sample_rows':[[str(v) if v is not None else '' for v in row] for row in sheet.iter_rows(min_row=header_row+1,max_row=min(sheet.max_row,header_row+3),values_only=True)]})
            return {'document_id':document_id,'kind':'xlsx','sheets':sheets}
        except DomainError:raise
        except Exception as exc:raise DomainError('EXCEL_PARSE_INVALID','无法读取 Excel 工作表与表头',422) from exc
        finally:book.close()
    def _excel(self,path,selection):
        from openpyxl import load_workbook
        from openpyxl.utils.cell import column_index_from_string
        try:
            book=load_workbook(path,data_only=False,read_only=True)
            if selection.get('sheet') not in book.sheetnames:raise ValueError()
            sheet=book[selection['sheet']];header=int(selection.get('header_row',1));start=int(selection.get('row_start',header+1));end=int(selection.get('row_end',sheet.max_row))
            content=selection.get('content_columns') or [];parameters=selection.get('parameter_columns') or []
            if not content or not isinstance(content,list) or not isinstance(parameters,list) or not 0<=header<start<=end<=sheet.max_row or end-start>10000:raise ValueError()
            cols=list(dict.fromkeys(content+parameters));indexes=[column_index_from_string(c) for c in cols]
            if any(i>sheet.max_column for i in indexes):raise ValueError()
            headings=[str(sheet.cell(header,i).value or cols[n]) if header else cols[n] for n,i in enumerate(indexes)]
            result=[]
            for row in range(start,end+1):
                values=[]
                for col,i,label in zip(cols,indexes,headings):
                    cell=sheet.cell(row,i)
                    if cell.data_type=='f':raise DomainError('FORMULA_NOT_SUPPORTED','所选知识单元格包含公式，请提供固定值',422,{'cell':cell.coordinate})
                    if cell.value is not None:values.append(f'{label}: {cell.value}')
                if values:result.append({'text':'\n'.join(values),'locator':{'kind':'xlsx','sheet':sheet.title,'row':row,'cells':','.join(f'{c}{row}' for c in cols)}})
            book.close();return result
        except DomainError:raise
        except Exception as e:raise DomainError('EXCEL_SELECTION_INVALID','请核对工作表、表头、内容列、参数列及行范围',422) from e
    def default_profile(self):
        cfg=self.settings.local
        return {'id':'local-default','name':'默认本地向量模型','mode':'local','model':cfg['embedding_path'],'device':cfg['embedding_device'],'max_length':cfg['embedding_max_length'],'batch_size':cfg['embedding_batch_size'],'max_chars':12000,'configured':True,'readonly':True}
    def profile(self,ident):return self.default_profile() if ident=='local-default' else self.repo.get('embedding_profile',ident)
    def profiles(self):
        return [{**p,'used_by_libraries':[{'id':l['id'],'name':l['name']} for l in self.libraries() if l.get('active_snapshot_id') and self.repo.get('knowledge_snapshot',l['active_snapshot_id']).get('embedding_profile',{}).get('id','local-default')==p['id']]} for p in [self.default_profile(),*self.repo.list('embedding_profile')]]
    def create_profile(self,body):
        mode=body.get('mode');model=str(body.get('model','')).strip();name=str(body.get('name') or model).strip();max_chars=body.get('max_chars',1000)
        if mode!='local':raise DomainError('LOCAL_EMBEDDING_ONLY','当前版本仅支持本地向量模型',422)
        if not model or not name or not isinstance(max_chars,int) or isinstance(max_chars,bool) or not 100<=max_chars<=12000:raise DomainError('EMBEDDING_CONFIG_INVALID','请提供本地模型及有效切片上限',422)
        if not Path(model).is_dir():raise DomainError('EMBEDDING_FILES_MISSING','本地模型目录不存在',422)
        record=self.repo.put('embedding_profile',{'name':name,'mode':'local','model':model,'max_chars':max_chars,'max_length':int(body.get('max_length',1024)),'device':'cpu','batch_size':int(body.get('batch_size',4)),'configured':True})
        return self.public_profile(record)
    @staticmethod
    def public_profile(profile):return {k:v for k,v in profile.items() if k!='key_env'}
    def check_profile(self,ident,allow_cloud=False):
        profile=self.profile(ident)
        if profile['mode']!='local':raise DomainError('LOCAL_EMBEDDING_ONLY','当前版本仅支持本地向量模型',422)
        embed=self.embedding(profile);vectors=embed.embed(['Embedding connection check'])
        return {'status':'connected','dimensions':len(vectors[0]),'fingerprint':embed.fingerprint,'mode':profile['mode']}
    def embedding(self,profile):
        if profile['mode']!='local':raise DomainError('LOCAL_EMBEDDING_ONLY','当前版本仅支持本地向量模型',422)
        return ProfileEmbedding(profile,self.settings)
    def draft_signature(self,library_id):
        return content_hash([(d['id'],d.get('enabled',True),d.get('rule_draft'),d.get('draft_discarded',False)) for d in self.repo.list('kb_document',parent_id=library_id)])
    def enqueue_build(self,library_id,body):
        lib=self.library(library_id)
        self._available(lib,enabled=True)
        if lib.get('readonly') or not lib.get('enabled'):raise DomainError('LIBRARY_DISABLED','知识库只读或已停用',409)
        ids=body.get('preview_ids') or []
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):raise DomainError('PREVIEW_REQUIRED','请选择完整预览后再构建',422)
        previews=[self.repo.get('kb_preview',i) for i in ids];profile=self.profile(body.get('embedding_profile_id','local-default'))
        if profile['mode']!='local':raise DomainError('LOCAL_EMBEDDING_ONLY','当前版本仅支持本地向量模型',422)
        if profile['mode']=='cloud' and not profile.get('configured'):raise DomainError('EMBEDDING_KEY_MISSING','云Embedding尚未配置密钥',422)
        docs=set()
        for preview in previews:
            doc=self.repo.get('kb_document',preview['document_id'])
            if preview['library_id']!=library_id or not doc.get('enabled') or doc['id'] in docs:raise DomainError('PREVIEW_NOT_ALLOWED','预览不属于当前库或文档重复/停用',422)
            if file_hash(controlled_path(self.repo.data_dir,doc['managed_path']))!=preview['file_sha256']:raise DomainError('FILE_CHANGED','构建原件已变化，请重新上传并预览',409)
            self.validate_current_preview(preview,doc)
            self.validate_preview_budget(preview,profile)
            docs.add(doc['id'])
            if max(map(lambda c:len(c['text']),preview['chunks']))>profile['max_chars']:raise DomainError('EMBEDDING_BUDGET','切片超过所选模型预算，请重新预览，不能静默截断',422)
        if lib.get('active_snapshot_id'):
            base=self.repo.get('knowledge_snapshot',lib['active_snapshot_id'])
            for source_id in base['source_version_ids']:
                source=self.repo.get('source_version',source_id)
                if source.get('document_id') not in docs and self._document_enabled(source_id):
                    if any(len(c['text'])>profile['max_chars'] for c in self.repo.list('chunk',parent_id=source_id,limit=100000)):raise DomainError('EMBEDDING_BUDGET','保留来源超过所选档案上限，请重新预览该文档',422)
        with self.repo.transaction():
            self._available(self.library(library_id),enabled=True)
            build=self.repo.put('kb_build',{'library_id':library_id,'preview_ids':ids,'embedding_profile':dict(profile),'base_snapshot_id':lib.get('active_snapshot_id'),'status':'queued','stage':'queued','progress':None,'started_at':None,'finished_at':None,'document_count':len(ids),'prepared_document_count':0,'snapshot_id':None,'draft_signature':self.draft_signature(library_id),'retained_source_ids':[i for i in (self.repo.get('knowledge_snapshot',lib['active_snapshot_id'])['source_version_ids'] if lib.get('active_snapshot_id') else []) if self._document_enabled(i) and self.repo.get('source_version',i).get('document_id') not in docs]},parent_id=library_id)
            job=self.repo.put('job',{'kind':'kb_build','build_id':build['id'],'status':'queued','cancel_requested':False},parent_id=build['id'])
            self.repo.update('kb_build',build['id'],{'job_id':job['id']})
            return {**self.repo.get('kb_build',build['id']),'build_id':build['id'],'job_id':job['id']}
    def execute_build(self,build_id,embedding=None):
        build=self.repo.get('kb_build',build_id);lib=self.library(build['library_id'])
        self.repo.update('kb_build',build_id,{'status':'running','stage':'preparing','progress':None,'started_at':utc_now(),'finished_at':None,'error':None})
        try:
            self._check_build(build_id)
            replacing={self.repo.get('kb_preview',i)['document_id'] for i in build['preview_ids']}
            source_ids=[]
            if build.get('base_snapshot_id'):
                base=self.repo.get('knowledge_snapshot',build['base_snapshot_id'])
                source_ids=build.get('retained_source_ids',[i for i in base['source_version_ids'] if self.repo.get('source_version',i).get('document_id') not in replacing])
            for source_id in source_ids:
                self._check_build(build_id)
                if any(len(c['text'])>build['embedding_profile']['max_chars'] for c in self.repo.list('chunk',parent_id=source_id,limit=100000)):
                    raise DomainError('EMBEDDING_BUDGET','旧文档切片超过新模型预算，请重新预览全部保留文档',422)
            for n,ident in enumerate(build['preview_ids']):
                self._check_build(build_id)
                preview=self.repo.get('kb_preview',ident);doc=self.repo.get('kb_document',preview['document_id'])
                path=controlled_path(self.repo.data_dir,doc['managed_path'])
                if file_hash(path)!=preview['file_sha256']:raise DomainError('FILE_CHANGED','构建原件已变化',409)
                with self.repo.transaction():
                    source=self.repo.put('source_version',{'product_id':lib['product_id'],'library_id':lib['id'],'document_id':doc['id'],'source_name':doc['name'],'document_type':doc.get('document_type','product_description'),'version_label':build_id[:8],'file_sha256':doc['file_sha256'],'parser_fingerprint':preview['content_hash'],'managed_path':doc['managed_path'],'status':'valid','chunk_count':preview['chunk_count'],'effective_config':preview.get('effective_config'),'parser_version':preview.get('parser_version')})
                    for c in preview['chunks']:self.repo.put('chunk',{**c,'source_version_id':source['id'],'product_id':lib['product_id'],'content_hash':content_hash(c['text'])},parent_id=source['id'])
                source_ids.append(source['id']);self.repo.update('kb_build',build_id,{'prepared_document_count':n+1})
                self._check_build(build_id)
            knowledge=KnowledgeService.from_settings(self.repo,self.settings)
            snapshot=self.repo.put('knowledge_snapshot',{'product_id':lib['product_id'],'library_id':lib['id'],'source_version_ids':source_ids,'status':'parsed','embedding_profile':build['embedding_profile']},parent_id=lib['id'])
            self.repo.update('kb_build',build_id,{'stage':'embedding','chunk_count':sum(len(self.repo.list('chunk',parent_id=i,limit=100000)) for i in source_ids)})
            from app.rag.retrieval import HybridRetriever
            adapter=embedding or self.embedding(build['embedding_profile'])
            service=self
            class CancellableEmbedding:
                @property
                def fingerprint(self):return adapter.fingerprint
                def embed(self,texts,query=False):
                    service._check_build(build_id);vectors=adapter.embed(texts,query=query);service._check_build(build_id);service.repo.update('kb_build',build_id,{'stage':'indexing'});return vectors
            self._check_build(build_id)
            HybridRetriever(self.repo,knowledge,CancellableEmbedding()).build(snapshot['id'],activate=False)
            with self.repo.transaction():
                self._check_build(build_id)
                self.repo.update('kb_build',build_id,{'status':'ready','stage':'completed','progress':None,'finished_at':utc_now(),'snapshot_id':snapshot['id']})
            return self.repo.get('kb_build',build_id)
        except Exception as e:
            code=getattr(e,'code',None)
            self.repo.update('kb_build',build_id,{'status':'cancelled' if code=='BUILD_CANCELLED' else ('interrupted' if code=='EXECUTION_OFFLINE' else 'failed'),'error':{'code':code or 'BUILD_FAILED','message':getattr(e,'message','构建失败，请检查文件与向量模型连接')},'finished_at':utc_now()})
            raise
    def publish(self,build_id,expected_version):
        with self.repo.transaction():
            build=self.repo.get('kb_build',build_id)
            if build['version']!=expected_version:raise DomainError('VERSION_CONFLICT','构建版本已变化，请刷新',409)
            if build.get('draft_abandoned'):raise DomainError('DRAFT_DISCARDED','此构建所属草稿已放弃，请重新构建',409)
            if build['status']!='ready':raise DomainError('INDEX_NOT_READY','构建未成功，不能发布',409)
            snapshot=self.repo.get('knowledge_snapshot',build['snapshot_id'])
            if snapshot['status']!='ready':raise DomainError('INDEX_NOT_READY','索引尚未就绪',409)
            library=self.library(build['library_id'])
            self._available(library,enabled=True)
            if build.get('job_id'):
                job=self.repo.get('job',build['job_id'])
                if job.get('cancel_requested') or job['status']=='cancelled':raise DomainError('BUILD_CANCELLED','构建已取消，不能发布',409)
            if library.get('active_snapshot_id')!=build.get('base_snapshot_id'):raise DomainError('LIBRARY_HEAD_CHANGED','知识库已发布其他新版本，请重新构建后发布，避免覆盖新增文档',409)
            if build.get('draft_signature') and build['draft_signature']!=self.draft_signature(build['library_id']):raise DomainError('DRAFT_CHANGED','构建后草稿资料已变化，请重新构建再发布',409)
            self.repo.update('kb_build',build_id,{'published_at':utc_now()})
            return self.repo.update('kb_library',build['library_id'],{'active_snapshot_id':snapshot['id'],'wizard_draft':{},'has_draft':False,'draft_baseline':None})
    def task_snapshot(self,product_id,project_id=None):
        if project_id is None:
            if product_id not in {p['id'] for p in self.settings.products}:return None
            legacy=self.legacy('legacy:'+product_id)
            if not legacy.get('enabled') or legacy.get('trashed'):return None
            return KnowledgeService.from_settings(self.repo,self.settings).active(product_id)
        bound=self.bindings(project_id);children=[];versions=[]
        for ident in bound['library_ids']:
            lib=self.library(ident)
            if lib['product_id']!=product_id or not lib.get('enabled') or lib.get('trashed') or not lib.get('active_snapshot_id'):continue
            snapshot=self.repo.get('knowledge_snapshot',lib['active_snapshot_id'])
            if not snapshot['source_version_ids']:continue
            children.append(snapshot['id']);versions.append({'library_id':ident,'snapshot_id':snapshot['id'],'embedding_profile_id':snapshot.get('embedding_profile',{}).get('id','local-default')})
        if not children:return None
        if len(children)==1 and children[0] and versions[0]['library_id'].startswith('legacy:'):return self.repo.get('knowledge_snapshot',children[0])
        sources=list(dict.fromkeys(i for child in children for i in self.repo.get('knowledge_snapshot',child)['source_version_ids']))
        digest=content_hash({'product_id':product_id,'children':children})
        existing=[x for x in self.repo.list('knowledge_snapshot',limit=100000) if x.get('composite_hash')==digest]
        if existing:return existing[0]
        return self.repo.put('knowledge_snapshot',{'product_id':product_id,'source_version_ids':sources,'status':'ready','child_snapshot_ids':children,'library_versions':versions,'composite_hash':digest})
    def _document_enabled(self,source_id):
        source=self.repo.get('source_version',source_id)
        return not source.get('document_id') or (self.repo.get('kb_document',source['document_id']).get('enabled',True) and not self.repo.get('kb_document',source['document_id']).get('draft_discarded'))
    def search(self,library_id,query,project_id=None,build_id=None):
        from app.rag.retrieval import HybridRetriever
        from app.schemas.core import Claim
        if not isinstance(query,str) or not query.strip() or len(query)>12000:raise DomainError('QUERY_INVALID','请输入检索文本',422)
        lib=self.library(library_id)
        self._available(lib,enabled=True)
        if project_id and library_id not in self.bindings(project_id)['library_ids']:raise DomainError('LIBRARY_NOT_BOUND','该知识库未绑定当前项目',403)
        if build_id:
            build=self.repo.get('kb_build',build_id)
            if build['library_id']!=library_id or build['status']!='ready':raise DomainError('INDEX_NOT_READY','构建尚未就绪或不属于本库',409)
            snapshot=self.repo.get('knowledge_snapshot',build['snapshot_id'])
        else:
            if not lib.get('active_snapshot_id'):raise DomainError('INDEX_NOT_READY','知识库尚未发布可检索版本',409)
            snapshot=self.repo.get('knowledge_snapshot',lib['active_snapshot_id'])
        adapter=self.embedding(snapshot.get('embedding_profile',self.default_profile()))
        claim=Claim(id='test',clause_id='test',text=query,source_spans=[{'start':0,'end':len(query)}])
        refs=HybridRetriever(self.repo,KnowledgeService.from_settings(self.repo,self.settings),adapter).retrieve(claim,lib['product_id'],snapshot['id'])
        return {'items':[r.model_dump() for r in refs],'snapshot_id':snapshot['id']}

class ProfileEmbedding:
    def __init__(self,profile,settings):
        self.profile,self.settings=profile,settings;self.instance=None
    def _local(self):
        if self.instance is None:
            from app.rag.embeddings import LocalEmbedding
            self.instance=LocalEmbedding(self.profile['model'],self.profile.get('device','cpu'),self.profile.get('max_length',1024),self.profile.get('batch_size',4))
        return self.instance
    def _lease(self):
        from app.rag.resource_pool import embedding_pool,local_model_key
        from app.rag.embeddings import LocalEmbedding
        key,size=local_model_key(self.profile)
        return embedding_pool(str(self.settings.data_dir)).acquire(key,
            lambda:LocalEmbedding(self.profile['model'],self.profile.get('device','cpu'),self.profile.get('max_length',1024),self.profile.get('batch_size',4)),size)
    @property
    def fingerprint(self):
        if self.profile['mode']=='local':
            with self._lease() as instance:return instance.fingerprint
        return content_hash({k:v for k,v in self.profile.items() if k not in {'configured','key_env','created_at','updated_at','version'}})
    def embed(self,texts,query=False):
        if self.profile['mode']=='local':
            from app.rag.resource_pool import heavy_lease
            with heavy_lease(self.settings.data_dir,'embedding'),self._lease() as instance:
                return instance.embed(texts,query)
        from app.security.execution_policy import policy_for,assert_endpoint_allowed
        frozen=getattr(self.settings,'execution_policy',None)
        policy=policy_for(self.settings,frozen=frozen,extra_services={'embedding':[self.profile['base_url']]})
        assert_endpoint_allowed(policy,self.profile['base_url'],'embedding')
        if any(len(t)>self.profile['max_chars'] for t in texts):raise DomainError('EMBEDDING_BUDGET','文本超过云模型配置预算，未发送也未截断',422)
        from dotenv import dotenv_values
        key=dotenv_values(self.settings.configuration_root/'.env.local').get(self.profile.get('key_env','')) or self.settings.secrets.get(self.profile.get('key_env',''))
        if not key:raise DomainError('EMBEDDING_KEY_MISSING','请配置云Embedding密钥',422)
        import httpx,numpy as np
        try:
            with httpx.Client(timeout=60,follow_redirects=False,trust_env=False) as client:
                policy=policy_for(self.settings,frozen=frozen,extra_services={'embedding':[self.profile['base_url']]})
                assert_endpoint_allowed(policy,self.profile['base_url'],'embedding')
                response=client.post(self.profile['base_url']+'/embeddings',headers={'Authorization':'Bearer '+key},json={'model':self.profile['model'],'input':texts})
            response.raise_for_status();body=response.json();rows=sorted(body['data'],key=lambda x:x['index'])
            if [r['index'] for r in rows]!=list(range(len(texts))):raise ValueError('Embedding indexes mismatch')
            vectors=np.asarray([r['embedding'] for r in rows],dtype='float32')
            if vectors.ndim!=2 or len(vectors)!=len(texts) or not np.isfinite(vectors).all() or (np.linalg.norm(vectors,axis=1)==0).any():raise ValueError()
            return vectors
        except DomainError:raise
        except Exception as e:raise DomainError('EMBEDDING_UNAVAILABLE','云Embedding调用失败；请检查地址、凭据和文本预算',503) from e
