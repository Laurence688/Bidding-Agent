"""Explicit purge of unused custom libraries, with recoverable filesystem staging.

SQLite serializes lifecycle checks with task freezing and Worker claims. A durable
journal stages owned files before the database commit; retries restore an aborted
transaction or finish cleanup after a committed one. No success precedes cleanup.
"""
from pathlib import Path
import json
import os
import shutil
import stat
from app.domain.errors import DomainError
from app.domain.identifiers import utc_now


def references(value, identifiers):
    if isinstance(value,str):return any(ident in value for ident in identifiers)
    if isinstance(value,dict):return any(references(key,identifiers) or references(item,identifiers) for key,item in value.items())
    if isinstance(value,list):return any(references(item,identifiers) for item in value)
    return False


def checked_path(root,relative):
    """Check the lexical path as well as every existing ancestor, including junctions."""
    relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts:raise DomainError('PURGE_UNSAFE_PATH','资料路径超出受控知识目录，未删除任何资料',409)
    target=root/relative
    if not target.resolve().is_relative_to(root.resolve()):raise DomainError('PURGE_UNSAFE_PATH','资料路径超出受控知识目录，未删除任何资料',409)
    for item in [root,*[root/Path(*relative.parts[:i]) for i in range(1,len(relative.parts)+1)]]:
        if item.exists() or item.is_symlink():
            info=item.lstat()
            if item.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                raise DomainError('PURGE_UNSAFE_PATH','资料目录包含链接或重定向，未删除任何资料',409)
    return target


def checked_tree(root,relative):
    target=checked_path(root,relative)
    if target.is_dir():
        for child in target.rglob('*'):checked_path(root,child.relative_to(root))
    return target


class LibraryPurgeMixin:
    def _recover_uncommitted_purge(self,ident):
        if ident.startswith('legacy:'):return
        root=self.repo.data_dir;relative='library-purge/'+ident;stage=checked_tree(root,relative);journal=stage/'journal.json'
        if not stage.exists():return
        if not journal.exists():
            # Renaming begins only after the durable journal is installed.
            if (stage/'journal.pending').exists():(stage/'journal.pending').unlink()
            if any(stage.iterdir()):raise DomainError('PURGE_IO_FAILED','删除暂存目录缺少恢复日志，请检查后再恢复知识库',503)
            stage.rmdir();return
        try:recovery=json.loads(journal.read_text('utf-8'))
        except (OSError,ValueError) as error:raise DomainError('PURGE_IO_FAILED','删除恢复日志无法读取，请检查后再恢复知识库',503) from error
        if recovery.get('user_id')!=self.repo.local_user_id or recovery.get('library_id')!=ident:raise DomainError('PURGE_UNSAFE_PATH','删除恢复日志身份不匹配',409)
        entries=recovery.get('paths')
        if not isinstance(entries,list):raise DomainError('PURGE_UNSAFE_PATH','删除恢复日志格式无效',409)
        for entry in reversed(entries):
            if not isinstance(entry,dict) or not str(entry.get('staged','')).isdigit() or Path(entry.get('original','')).parts[:1] not in [('library-files',),('sources',),('indexes',)]:
                raise DomainError('PURGE_UNSAFE_PATH','删除恢复日志含无效文件位置',409)
            source=checked_tree(root,relative+'/'+entry['staged']);target=checked_path(root,entry['original'])
            if source.exists():
                if target.exists():raise DomainError('PURGE_IO_FAILED','原件位置已有文件，恢复未覆盖；请检查删除恢复日志',503)
                target.parent.mkdir(parents=True,exist_ok=True)
                try:os.replace(source,target)
                except OSError as error:raise DomainError('PURGE_IO_FAILED','原件仍在删除暂存区，未恢复启用；请稍后重试',503) from error
        journal.unlink();stage.rmdir()

    def _purge_plan(self,lib,db):
        # Retained/deleted history is intentional: soft deletion does not erase evidence obligations.
        rows=[{**self.repo.decode(row),'kind':row['kind'],'user_id':row['user_id']} for row in db.execute('SELECT * FROM records')]
        owned={lib['id']};snapshots=set()
        for row in rows:
            if row['user_id']==self.repo.local_user_id and row['kind'] in {'kb_document','kb_preview','kb_build','source_version','knowledge_snapshot'} and (row.get('library_id')==lib['id'] or row.get('parent_id')==lib['id']):owned.add(row['id'])
        # Own document/source/chunk/build/job edges. Composite snapshots are dependency
        # nodes, never business history on their own, and may themselves be unused.
        changed=True
        while changed:
            changed=False
            for row in rows:
                if row['id'] in owned:continue
                kind=row['kind'];data={k:v for k,v in row.items() if k not in {'id','created_at','updated_at'}}
                dependent=(kind in {'kb_preview','chunk','job','index_manifest'} and (row.get('parent_id') in owned or any(row.get(k) in owned for k in ['document_id','source_version_id','build_id','snapshot_id'])))
                dependent=dependent or kind=='source_version' and row.get('document_id') in owned
                dependent=dependent or kind=='knowledge_snapshot' and references(data,owned)
                dependent=dependent or kind=='config_snapshot' and references(data,owned)
                if not dependent:continue
                if row['user_id']!=self.repo.local_user_id:raise DomainError('LIBRARY_SHARED','存在其他用户共享的资料关联，不能永久删除',409)
                if kind=='knowledge_snapshot' and row.get('library_id') not in {None,lib['id']}:
                    raise DomainError('LIBRARY_SHARED','资料已被其他知识库版本共享，请先解除共享关联',409)
                owned.add(row['id']);changed=True
                if kind=='knowledge_snapshot':snapshots.add(row['id'])
        snapshots.update(row['id'] for row in rows if row['kind']=='knowledge_snapshot' and row['id'] in owned)
        # A stale project binding, frozen run, QA, evaluation, citation, or other
        # external record is a blocker. Lifecycle audit entries remain as audit only.
        blockers=[]
        for row in rows:
            if row['id'] in owned or row['kind'] in {'audit','library_purge'}:continue
            if references({k:v for k,v in row.items() if k not in {'id','created_at','updated_at','managed_path'}},owned):
                blockers.append({'kind':row['kind'],'id':row['id']})
        for table,column in [('node_commits','output'),('model_attempts','data')]:
            for row in db.execute(f'SELECT {column} FROM {table}'):
                if references(json.loads(row[column]),owned):blockers.append({'kind':table})
        if blockers:raise DomainError('LIBRARY_REFERENCED','此知识库仍被历史任务、引用或项目绑定使用，不能永久删除；请保留以便查看原始来源',409,{'references':blockers[:10],'reference_count':len(blockers)})
        for row in rows:
            if row['id'] not in owned:continue
            if row['kind']=='kb_build' and row.get('status') in {'queued','running','building','cancelling'}:
                raise DomainError('BUILD_STOP_REQUIRED','请先等待构建确认停止，再永久删除',409)
            if row['kind']=='job' and row.get('status') in {'queued','running','cancelling'}:
                raise DomainError('BUILD_STOP_REQUIRED','后台任务尚未确认停止，不能永久删除',409)
        shared_paths={row['managed_path'] for row in rows if row['id'] not in owned and row.get('managed_path')}
        paths=set()
        for row in rows:
            if row['id'] not in owned or not row.get('managed_path'):continue
            name=row['managed_path'];relative=Path(name)
            if relative.is_absolute() or len(relative.parts)<2 or relative.parts[0] not in {'library-files','sources'}:
                raise DomainError('PURGE_UNSAFE_PATH','原件不属于受控知识库文件目录，不能永久删除',409)
            target=checked_tree(self.repo.data_dir,name)
            # Check canonical identity too: a shared alias must preserve the same file.
            if any(checked_path(self.repo.data_dir,p).resolve()==target.resolve() for p in shared_paths):continue
            paths.add(relative.as_posix())
        for ident in snapshots:
            if not ident or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in ident):
                raise DomainError('PURGE_UNSAFE_PATH','索引标识无效，未删除任何资料',409)
            relative='indexes/'+ident;checked_tree(self.repo.data_dir,relative);paths.add(relative)
        return owned,snapshots,sorted(paths)

    def purge(self,ident,expected_version,confirmation_name):
        if getattr(self.repo._local,'db',None) is not None:
            raise DomainError('PURGE_TRANSACTION','永久删除必须独立提交，不能嵌入其他操作',409)
        root=self.repo.data_dir;record_id='library-purge:'+ident
        if not ident or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in ident):
            # Compatibility libraries are configured sources, not custom uploads.
            lib=self.library(ident)
            raise DomainError('LIBRARY_CONFIG_OWNED','既有产品库由本地配置管理，请保留回收状态；不能通过此操作删除配置来源',409)
        relative='library-purge/'+ident;stage=checked_tree(root,relative);journal=stage/'journal.json'
        moved=[];committed=False;journal_created=False
        try:
            with self.repo.transaction() as db:
                try:pending=self.repo.get('library_purge',record_id)
                except DomainError as error:
                    if error.code!='NOT_FOUND':raise
                    pending=None
                if pending:
                    if pending['expected_version']!=expected_version:raise DomainError('VERSION_CONFLICT','删除确认版本已变化，请刷新',409)
                    if pending['name']!=confirmation_name:raise DomainError('LIBRARY_NAME_MISMATCH','请输入完整知识库名称确认删除',422)
                    if pending.get('status')=='completed':return {'deleted':True,'library_id':ident}
                    committed=True
                else:
                    lib=self.library(ident)
                    if lib['version']!=expected_version:raise DomainError('VERSION_CONFLICT','知识库已变化，请刷新后重新确认',409)
                    if not lib.get('trashed'):raise DomainError('LIBRARY_NOT_TRASHED','只有回收站中的知识库可以永久删除',409)
                    if confirmation_name!=lib['name']:raise DomainError('LIBRARY_NAME_MISMATCH','请输入完整知识库名称确认删除',422)
                    # A previous process may have died before its SQLite transaction
                    # committed. Restore every staged path before reassessing references.
                    self._recover_uncommitted_purge(ident)
                    owned,snapshots,paths=self._purge_plan(lib,db)
                    stage.mkdir(parents=True,exist_ok=True)
                    entries=[{'original':p,'staged':str(i)} for i,p in enumerate(paths) if checked_path(root,p).exists()]
                    payload={'user_id':self.repo.local_user_id,'library_id':ident,'paths':entries}
                    temporary=stage/'journal.pending'
                    with temporary.open('w',encoding='utf-8') as stream:
                        json.dump(payload,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
                    os.replace(temporary,journal)
                    journal_created=True
                    for entry in entries:
                        source=checked_tree(root,entry['original']);target=checked_path(root,relative+'/'+entry['staged'])
                        os.replace(source,target);moved.append((source,target))
                    self.repo.put('library_purge',{'library_id':ident,'name':lib['name'],'product_id':lib['product_id'],'expected_version':expected_version,'status':'cleanup_pending'},record_id=record_id)
                    self.repo.audit(None,'library.purge',{'library_id':ident,'previous_version':expected_version,'record_count':len(owned)})
                    for snapshot in snapshots:db.execute('DELETE FROM chunk_fts WHERE snapshot_id=?',(snapshot,))
                    for row in db.execute('SELECT rowid,response FROM idempotency_records').fetchall():
                        if references(json.loads(row['response']),owned):db.execute('DELETE FROM idempotency_records WHERE rowid=?',(row['rowid'],))
                    for rowid in owned:db.execute('DELETE FROM job_events WHERE job_id=?',(rowid,))
                    # FK parents require children first, including deleted rows.
                    remaining=set(owned)
                    while remaining:
                        leaves=[value for value in remaining if not db.execute('SELECT 1 FROM records WHERE parent_id=? OR project_id=? LIMIT 1',(value,value)).fetchone()]
                        if not leaves:raise DomainError('LIBRARY_REFERENCED','存在其他资料关联，不能永久删除',409)
                        for value in leaves:db.execute('DELETE FROM records WHERE id=? AND user_id=?',(value,self.repo.local_user_id));remaining.remove(value)
            committed=True
        except BaseException as error:
            if not committed:
                try:
                    for source,target in reversed(moved):
                        checked_path(root,source.relative_to(root));checked_tree(root,target.relative_to(root))
                        if source.exists():raise OSError('original location is occupied')
                        os.replace(target,source)
                    if journal_created:
                        if journal.exists():journal.unlink()
                        if stage.exists():stage.rmdir()
                except OSError:
                    raise DomainError('PURGE_IO_FAILED','删除已回滚，但部分原件待恢复；请再次确认删除以自动恢复并重试',503) from error
            if isinstance(error,OSError):raise DomainError('PURGE_IO_FAILED','文件暂时无法移除，删除已回滚，请稍后重试',503) from error
            raise
        try:
            if stage.exists():shutil.rmtree(checked_tree(root,relative))
        except OSError as error:
            raise DomainError('PURGE_CLEANUP_PENDING','知识库记录已移除，但文件清理尚未完成；请再次确认重试，暂不视为删除完成',503) from error
        self.repo.update('library_purge',record_id,{'status':'completed','finished_at':utc_now()})
        return {'deleted':True,'library_id':ident}
