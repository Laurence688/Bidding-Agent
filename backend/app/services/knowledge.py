from pathlib import Path
import shutil
from app.domain.errors import DomainError
from app.domain.identifiers import file_hash, content_hash, new_id
from app.rag.parsers import parse_document, PARSER_VERSION
from app.rag.chunking import chunk_blocks
from app.rag.structured import parse_structured, SCHEMA_VERSION
from app.rag.pagination import load_pagination, pagination_fingerprint
from app.rag.validation import validate_records, records_digest
from app.schemas.core import EvidenceRef
from app.security.local_access import controlled_path


class KnowledgeService:
    def __init__(self, repo, source_root, allowlist):
        self.repo, self.root, self.allowlist = repo, Path(source_root).resolve(), allowlist
        self.lifecycle_guard = None
        self._table_scope_cache = {}
        self._pdf_hash_cache = {}

    @classmethod
    def from_settings(cls, repo, config):
        root = Path(config.local['knowledge_root'])
        return cls(repo, root if root.is_absolute() else config.root / root, config.read_yaml('knowledge_sources.yaml')['sources'])

    def check_lifecycle(self,product_id,snapshot=None):
        if snapshot and (snapshot.get('library_id') or snapshot.get('child_snapshot_ids')):return
        if self.lifecycle_guard:self.lifecycle_guard()
        try:state=self.repo.get('kb_library_state','state:legacy:'+product_id)
        except DomainError as exc:
            if exc.code=='NOT_FOUND':return
            raise
        if state.get('trashed'):raise DomainError('LIBRARY_TRASHED','知识库已移至回收站',409)
        if not state.get('enabled',True):raise DomainError('LIBRARY_DISABLED','知识库已停用',409)

    def ingest(self, product_id, selection=None):
        self.check_lifecycle(product_id)
        entries = [e for e in self.allowlist if e['product_id'] == product_id]
        if selection is not None:
            if not set(selection).issubset({e['path'] for e in entries}):
                raise DomainError('SOURCE_NOT_ALLOWED', '来源不在该产品的授权清单', 422)
            entries = [e for e in entries if e['path'] in selection]
        if not entries:
            raise DomainError('SOURCE_NOT_ALLOWED', '没有可导入的授权来源', 422)
        versions = []
        try:
            for entry in entries:
                self.check_lifecycle(product_id)
                path = controlled_path(self.root, entry['path'])
                if not path.is_file():
                    raise DomainError('SOURCE_MISSING', '授权来源不存在', 422, {'source_name': Path(entry['path']).name})
                digest = file_hash(path)
                if entry.get('parser') not in {None, SCHEMA_VERSION}:
                    raise DomainError('PARSER_VERSION_UNSUPPORTED','来源解析器版本不受当前代码支持',422)
                structured = entry.get('parser') == SCHEMA_VERSION
                pagination = load_pagination(path,self.root.parent/'rendered') if structured else {}
                parser = content_hash({'parser':SCHEMA_VERSION,'pagination':pagination_fingerprint(pagination)}) if structured else PARSER_VERSION
                existing = [s for s in self.repo.list('source_version', limit=100000) if s['file_sha256'] == digest and s['product_id'] == product_id and s['document_type'] == entry['document_type'] and s['parser_fingerprint'] == parser]
                if existing:
                    if existing[0]['status'] != 'valid':
                        raise DomainError('SOURCE_REVOKED','该来源版本已失效，不能重建为有效证据',422)
                    if structured:
                        validate_records(self.repo.list('chunk',parent_id=existing[0]['id'],limit=100000),entry.get('expected_numbers'),
                                         existing[0].get('validation',{}).get('page_count'),entry['document_type'])
                    versions.append(existing[0]['id'])
                    continue
                source_id = new_id()
                target = controlled_path(self.repo.data_dir, f'sources/{source_id}/{path.name}')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                if file_hash(target) != digest:
                    raise DomainError('SOURCE_CHANGED', '来源读取期间发生变化，请重试', 409)
                blocks = parse_structured(target,product_id,entry['document_type'],pagination) if structured else list(chunk_blocks(parse_document(target)))
                if not blocks:
                    raise DomainError('SOURCE_EMPTY','来源没有解析出有效记录',422)
                validation=None
                if structured:
                    page_count=pagination.get('page_count')
                    if target.suffix.lower()=='.pdf':
                        from pypdf import PdfReader
                        page_count=len(PdfReader(target).pages)
                    validation=validate_records(blocks,entry.get('expected_numbers'),page_count,entry['document_type'])
                with self.repo.transaction():
                    source = self.repo.put('source_version', {'product_id': product_id, 'source_name': path.name,
                        'document_type': entry['document_type'], 'version_label': '未标注', 'file_sha256': digest,
                        'content_hash': content_hash([b['text'] for b in blocks]), 'parser_fingerprint': parser,
                        'schema_version': SCHEMA_VERSION if structured else PARSER_VERSION,
                        'pagination_fingerprint':pagination_fingerprint(pagination),
                        'rendered_pdf':blocks[0]['locator'].get('rendered_pdf'),
                        'rendered_pdf_sha256':pagination.get('pdf_sha256'), 'validation':validation,
                        'retrieval_chunk_count':sum(b.get('retrieval_eligible',True) for b in blocks),
                        'managed_path': str(target.relative_to(self.repo.data_dir)), 'status': 'valid', 'chunk_count': len(blocks)}, record_id=source_id)
                    for block in blocks:
                        self.repo.put('chunk', {**block, 'source_version_id': source_id, 'product_id': product_id,
                                              'content_hash': content_hash(block['text'])}, parent_id=source_id,record_id=block.get('chunk_id'))
                versions.append(source['id'])
            return self.build_snapshot(product_id, list(dict.fromkeys(versions)))
        except Exception as exc:
            self.repo.put('knowledge_error', {'product_id': product_id, 'code': getattr(exc, 'code', 'SOURCE_PARSE_FAILED'),
                                            'message': getattr(exc, 'message', '知识重建失败')})
            raise

    def build_snapshot(self, product_id, source_version_ids):
        self.check_lifecycle(product_id)
        for ident in source_version_ids:
            source = self.repo.get('source_version', ident)
            if source['product_id'] != product_id or source['status'] != 'valid':
                raise DomainError('SOURCE_INVALID', '来源产品或状态不适用', 422)
        return self.repo.put('knowledge_snapshot', {'product_id': product_id, 'source_version_ids': source_version_ids,
                            'parser_fingerprint': content_hash([self.repo.get('source_version',ident).get('parser_fingerprint',PARSER_VERSION) for ident in source_version_ids]), 'status': 'parsed', 'embedding_fingerprint': None})

    def activate(self, snapshot_id):
        snapshot = self.repo.get('knowledge_snapshot', snapshot_id)
        self.validate_snapshot(snapshot_id)
        if any(self.repo.get('source_version',ident).get('validation') for ident in snapshot['source_version_ids']) and snapshot['status']!='ready':
            raise DomainError('INDEX_NOT_READY','结构化快照尚未完成索引，不能激活',422)
        with self.repo.transaction():
            self.check_lifecycle(snapshot['product_id'],snapshot)
            heads = [h for h in self.repo.list('knowledge_head') if h['product_id'] == snapshot['product_id']]
            if heads:
                self.repo.update('knowledge_head', heads[0]['id'], {'snapshot_id': snapshot_id})
            else:
                self.repo.put('knowledge_head', {'product_id': snapshot['product_id'], 'snapshot_id': snapshot_id})

    def active(self, product_id):
        heads = [h for h in self.repo.list('knowledge_head') if h['product_id'] == product_id]
        return self.repo.get('knowledge_snapshot', heads[0]['snapshot_id']) if heads else None

    def chunks(self, snapshot_id):
        snapshot = self.repo.get('knowledge_snapshot', snapshot_id)
        return [c for ident in snapshot['source_version_ids'] for c in sorted(self.repo.list('chunk', parent_id=ident, limit=100000),key=lambda row:row.get('source_order',0))]

    def validate_snapshot(self,snapshot_id):
        snapshot=self.repo.get('knowledge_snapshot',snapshot_id)
        for ident in snapshot['source_version_ids']:
            source=self.repo.get('source_version',ident)
            if source['status']!='valid' or source['product_id']!=snapshot['product_id']:
                raise DomainError('SOURCE_INVALID','来源状态或型号不适用',422)
            validation=source.get('validation')
            if validation:
                rows=self.repo.list('chunk',parent_id=ident,limit=100000)
                validate_records(rows,validation['expected_numbers'],validation['page_count'],validation['document_type'])
                if records_digest(rows)!=validation['records_digest']:
                    raise DomainError('KNOWLEDGE_CHANGED','已验证记录发生变化，不能发布索引',409)
            if not self._rendered_pdf_valid(source):
                raise DomainError('PAGINATION_CHANGED','绑定的固定 PDF 已变化',409)

    def _rendered_pdf_valid(self,source):
        if source.get('library_id'):
            try:
                original=controlled_path(self.repo.data_dir,source['managed_path'])
                stat=original.stat();key=(str(original),stat.st_size,stat.st_mtime_ns)
                if key not in self._pdf_hash_cache:self._pdf_hash_cache[key]=file_hash(original)
                if self._pdf_hash_cache[key]!=source.get('file_sha256'):return False
            except (OSError,DomainError,KeyError):return False
        if not source.get('rendered_pdf_sha256'):return True
        try:
            path=controlled_path(self.root.parent/'rendered',source['rendered_pdf'])
            stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
            if key not in self._pdf_hash_cache:self._pdf_hash_cache[key]=file_hash(path)
            return self._pdf_hash_cache[key]==source['rendered_pdf_sha256']
        except (OSError,DomainError):return False

    def evidence(self, chunk_id, quote=None):
        chunk = self.repo.get('chunk', chunk_id)
        source = self.repo.get('source_version', chunk['source_version_id'])
        return EvidenceRef(id=chunk_id, chunk_id=chunk_id, source_version_id=source['id'], locator=chunk['locator'],
            quote=quote if quote is not None else chunk['text'], product_id=source['product_id'],
            document_type=source['document_type'], version_label=source['version_label'], source_name=source['source_name'])

    def verify_reference(self, ref):
        try:
            actual = self.evidence(ref.chunk_id)
            source = self.repo.get('source_version', ref.source_version_id)
            return (source['status'] == 'valid' and self._rendered_pdf_valid(source) and actual.source_version_id == ref.source_version_id
                    and actual.product_id == ref.product_id and actual.locator == ref.locator
                    and bool(ref.quote.strip()) and ref.quote in actual.quote)
        except DomainError:
            return False

    def product_scope_text(self, ref):
        parts = [ref.quote, ref.locator.section or '']
        if ref.locator.kind == 'docx' and ref.locator.table:
            key = (ref.source_version_id, ref.locator.table)
            if key not in self._table_scope_cache:
                # 单独的数值行可能丢失列头里的子型号；从同一不可变来源补查前三行。
                rows = [c for c in self.repo.list('chunk', parent_id=ref.source_version_id, limit=100000)
                        if c['locator'].get('table') == ref.locator.table and (c['locator'].get('row') or 99999) <= 3]
                self._table_scope_cache[key] = ' '.join(c['text'] for c in rows)
            parts.append(self._table_scope_cache[key])
        return ' '.join(parts)

    def revoke(self, source_id):
        return self.repo.update('source_version', source_id, {'status': 'revoked'})
