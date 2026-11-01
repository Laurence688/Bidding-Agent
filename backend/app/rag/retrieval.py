import json
import faiss
import numpy as np
from app.domain.identifiers import canonical
from app.domain.errors import DomainError
from app.rag.indexes import lexical_tokens, build_vectors, rrf, exact_terms
from app.domain.parameters import is_parameter_requirement


class HybridRetriever:
    def __init__(self, repo, knowledge, embedding, top_k=8, rrf_k=60):
        self.repo, self.knowledge, self.embedding = repo, knowledge, embedding
        self.top_k, self.rrf_k = top_k, rrf_k

    def build(self, snapshot_id, activate=True, vectors=None):
        self.knowledge.validate_snapshot(snapshot_id)
        snapshot = self.repo.get('knowledge_snapshot', snapshot_id)
        self.knowledge.check_lifecycle(snapshot['product_id'],snapshot)
        chunks = [c for c in self.knowledge.chunks(snapshot_id) if c.get('retrieval_eligible', True)]
        if not chunks:
            raise DomainError('INDEX_EMPTY', '知识快照没有有效片段', 422)
        if vectors is None:
            vectors = self.embedding.embed([c.get('embedding_text',c['text']) for c in chunks])
        self.knowledge.check_lifecycle(snapshot['product_id'],snapshot)
        vectors = np.asarray(vectors, dtype='float32')
        if vectors.ndim != 2 or len(vectors) != len(chunks) or not np.isfinite(vectors).all() or (np.linalg.norm(vectors,axis=1)==0).any():
            raise DomainError('EMBEDDING_INVALID','向量数量或值与知识片段不一致',422)
        index = build_vectors(vectors)
        folder = self.repo.data_dir / 'indexes' / snapshot_id
        folder.mkdir(parents=True, exist_ok=True)
        # Windows FAISS filename APIs do not reliably accept UTF-8 paths.
        # Python opens the Unicode path; callbacks retain streaming I/O.
        with (folder / 'vectors.faiss').open('wb') as stream:
            faiss.write_index(index, faiss.PyCallbackIOWriter(stream.write))
        term_index = {}
        for chunk in chunks:
            for term in exact_terms(chunk.get('fields',{}).get('rfc') or chunk.get('embedding_text',chunk['text'])):
                term_index.setdefault(term,[]).append(chunk['id'])
        manifest = {'fingerprint': self.embedding.fingerprint, 'product_id': snapshot['product_id'],
                    'exact_terms': term_index,
                    'snapshot_id': snapshot_id, 'chunk_ids': [c['id'] for c in chunks], 'dimensions': int(index.d)}
        (folder / 'manifest.json').write_text(canonical(manifest), 'utf-8')
        # 所有外部文件已完成后才切换 active；失败不会替换旧索引。
        with self.repo.transaction() as db:
            self.knowledge.check_lifecycle(snapshot['product_id'],snapshot)
            db.execute('DELETE FROM chunk_fts WHERE snapshot_id=?', (snapshot_id,))
            db.executemany('INSERT INTO chunk_fts VALUES(?,?,?,?)', [(c['id'], snapshot['product_id'], snapshot_id, lexical_tokens(c['text'])) for c in chunks])
            self.repo.put('index_manifest', manifest, parent_id=snapshot_id)
            self.repo.update('knowledge_snapshot',snapshot_id,{'status':'ready','embedding_fingerprint':self.embedding.fingerprint})
            if activate:
                self.knowledge.activate(snapshot_id)
        return manifest

    def retrieve(self, claim, product_id, snapshot_id, supplement=False):
        snapshot = self.repo.get('knowledge_snapshot', snapshot_id)
        if snapshot['product_id'] != product_id:
            raise DomainError('PRODUCT_CONFLICT', '知识快照与任务型号不一致', 422)
        folder = self.repo.data_dir / 'indexes' / snapshot_id
        if not (folder / 'manifest.json').exists():
            raise DomainError('INDEX_NOT_READY', '本地向量索引尚未构建完成', 503)
        manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
        if manifest['fingerprint'] != self.embedding.fingerprint or manifest['product_id'] != product_id:
            raise DomainError('INDEX_FINGERPRINT_MISMATCH', 'Embedding 配置已变化，需要重建索引', 409)
        query = self.embedding.embed([claim.text], query=True).copy()
        faiss.normalize_L2(query)
        count = self.top_k * (2 if supplement else 1)
        from app.rag.resource_pool import index_pool
        from app.domain.identifiers import content_hash
        vector_path=folder/'vectors.faiss'
        stat=vector_path.stat()
        key=(snapshot_id,content_hash(manifest),stat.st_size,stat.st_mtime_ns)
        def load_index():
            with vector_path.open('rb') as stream:
                return faiss.read_index(faiss.PyCallbackIOReader(stream.read))
        with index_pool(str(self.repo.data_dir)).acquire(key,load_index,stat.st_size) as index:
            _, positions = index.search(query, min(count * 3, len(manifest['chunk_ids'])))
        vector_ids = [manifest['chunk_ids'][int(i)] for i in positions[0] if i >= 0]
        terms = list(dict.fromkeys(lexical_tokens(claim.text).split()))[:64]
        lexical_ids = []
        if terms:
            expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
            with self.repo.connection() as db:
                lexical_ids = [row['chunk_id'] for row in db.execute(
                    'SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? AND product_id=? AND snapshot_id=? ORDER BY bm25(chunk_fts) LIMIT ?',
                    (expression, product_id, snapshot_id, count * 3))]
        ranked = rrf([lexical_ids, vector_ids], self.rrf_k)
        exact_ids = list(dict.fromkeys(ident for term in exact_terms(claim.text) for ident in manifest.get('exact_terms',{}).get(term,[])))
        order = {ident:rank for rank,ident in enumerate(ranked)}
        exact_ids.sort(key=lambda ident: (order.get(ident,float('inf')),ident))
        merged = list(dict.fromkeys(exact_ids + ranked))
        if is_parameter_requirement(claim.text):
            # Rank within the product/snapshot candidate pool, preserving relevance order.
            # A source label is a retrieval preference, never an admission or support verdict.
            refs = [self.knowledge.evidence(ident) for ident in merged]
            refs.sort(key=lambda ref: ref.document_type != 'feature_list')
            return refs[:count]
        return [self.knowledge.evidence(ident) for ident in merged[:count]]
