from pathlib import Path
from app.domain.identifiers import new_id, file_hash
from app.domain.errors import DomainError
from app.security.local_access import controlled_path


class FileService:
    def __init__(self, repo):
        self.repo = repo

    def upload(self, project_id, name, content, purpose):
        self.repo.get('project', project_id)
        suffix = Path(name).suffix.lower()
        allowed = {'bidding': {'.xlsx'}, 'knowledge': {'.docx', '.pdf', '.xlsx'}, 'evaluation': {'.json'},
                   'document_task': {'.docx', '.pdf', '.xlsx'}}
        if purpose not in allowed or suffix not in allowed[purpose]:
            raise DomainError('FILE_TYPE', '文件类型不适用于该用途', 415)
        if len(content) > 50 * 1024 * 1024:
            raise DomainError('FILE_SIZE', '文件超过 50 MiB 限制', 413)
        ident = new_id()
        path = controlled_path(self.repo.data_dir, f'files/{ident}{suffix}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return self.repo.put('file', {'name': Path(name).name, 'purpose': purpose, 'size': len(content),
            'managed_path': str(path.relative_to(self.repo.data_dir)), 'sha256': file_hash(path)}, project_id, record_id=ident)

    def resolve(self, file_id, project_id):
        record = self.repo.get('file', file_id, project_id)
        path = controlled_path(self.repo.data_dir, record['managed_path'])
        if not path.is_file() or file_hash(path) != record['sha256']:
            raise DomainError('FILE_CHANGED', '受控文件内容已变化，请重新导入', 409)
        return path
