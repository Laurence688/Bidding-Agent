"""Persistent knowledge creation rules and drafts, with immutable preview configs."""
from app.domain.errors import DomainError

class LibraryWizardMixin:
    def split_config(self,body):
        strategy=body.get('strategy','default');size=body.get('max_chars',1000);overlap=body.get('overlap',0)
        if strategy not in {'default','excel','leaf_sections','paragraphs','fixed','delimiter','excel_records','whole_document'}:raise DomainError('SPLIT_STRATEGY','切分策略无效',422)
        if strategy=='whole_document':
            body={**body,'selection':None};size,overlap=1000,0
        if isinstance(size,bool) or not isinstance(size,int) or not 100<=size<=12000:raise DomainError('CHUNK_SIZE','切片长度需为100至12000字符',422)
        if isinstance(overlap,bool) or not isinstance(overlap,int) or not 0<=overlap<size:raise DomainError('CHUNK_OVERLAP','重叠必须大于等于0且小于切片长度',422)
        delimiter='' if strategy=='whole_document' else body.get('delimiter','')
        if not isinstance(delimiter,str) or len(delimiter)>1000 or (strategy=='delimiter' and not delimiter):raise DomainError('SPLIT_DELIMITER','请输入1至1000字符的字面分隔符',422)
        return {'strategy':strategy,'max_chars':size,'overlap':overlap,'delimiter':delimiter,'selection':body.get('selection'),'embedding_profile_id':body.get('embedding_profile_id','local-default'),'template_id':body.get('template_id'),'template_version':body.get('template_version')}
    def templates(self):return self.repo.list('kb_split_template')
    def create_template(self,body):
        name=str(body.get('name','')).strip()
        if not name or len(name)>100:raise DomainError('TEMPLATE_NAME','模板名称需为1至100字符',422)
        return self.repo.put('kb_split_template',{'name':name,'config':self.split_config(body.get('config') or {})})
    def update_template(self,ident,body):
        changes={}
        if 'name' in body:
            name=str(body['name']).strip()
            if not name or len(name)>100:raise DomainError('TEMPLATE_NAME','模板名称需为1至100字符',422)
            changes['name']=name
        if 'config' in body:changes['config']=self.split_config(body['config'])
        return self.repo.update('kb_split_template',ident,changes,body['expected_version'])
    def delete_template(self,ident,version):
        with self.repo.transaction() as db:
            self.repo.update('kb_split_template',ident,{},version)
            db.execute('UPDATE records SET deleted=1 WHERE id=? AND user_id=?',(ident,self.repo.local_user_id))
        return {'deleted':True}
    def patch_library(self,ident,body):
        with self.repo.transaction():
            lib=self.library(ident);self._available(lib)
            changes={k:v for k,v in body.items() if k in {'enabled','name','description','wizard_draft'}}
            if lib.get('readonly') and set(changes)-{'enabled'}:raise DomainError('READ_ONLY','既有库仅支持状态管理',409)
            if 'name' in changes:
                changes['name']=changes['name'].strip()
                if not changes['name']:raise DomainError('LIBRARY_INVALID','请输入库名称',422)
            if lib['version']!=body['expected_version']:raise DomainError('VERSION_CONFLICT','内容已更新，请刷新',409)
            if 'wizard_draft' in changes:
                lib=self.start_draft(ident)
            return self._update_library(ident,changes,lib['version'])
    def patch_document(self,ident,body):
        with self.repo.transaction():
            doc=self.repo.get('kb_document',ident);self._available(self.library(doc['library_id']))
            changes={k:v for k,v in body.items() if k in {'enabled','rule_draft'}}
            self.start_draft(doc['library_id'])
            return self.repo.update('kb_document',ident,changes,body['expected_version'])
    def validate_preview_budget(self,preview,profile):
        chunks=preview['chunks']
        if not chunks:raise DomainError('SOURCE_EMPTY','所选范围没有可提取内容',422)
        longest=max(len(c['text']) for c in chunks)
        if longest>profile['max_chars']:raise DomainError('EMBEDDING_BUDGET',f"完整片段含 {longest:,} 字符，超过所选向量档案上限 {profile['max_chars']:,} 字符；请调整策略或档案，不会自动拆分或截断",422,{'max_chars':profile['max_chars'],'largest_chunk_chars':longest})
    def validate_current_preview(self,preview,doc):
        from app.domain.identifiers import content_hash
        draft=doc.get('rule_draft')
        if draft is not None and content_hash(self.split_config(draft))!=content_hash(preview.get('effective_config',self.split_config(preview))):raise DomainError('PREVIEW_STALE','文档规则已修改，请重新预览后构建',409)
