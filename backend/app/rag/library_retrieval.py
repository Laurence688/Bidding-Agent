from app.rag.retrieval import HybridRetriever
from app.rag.indexes import rrf
from app.services.libraries import ProfileEmbedding
from app.domain.errors import DomainError
from app.domain.retrieval_budget import select_feature_candidates

class LibraryRetriever:
    """Each immutable child index is queried with its own frozen model profile."""
    def __init__(self,repo,knowledge,embedding,settings,top_k=8,rrf_k=60):
        self.repo,self.knowledge,self.embedding,self.settings=repo,knowledge,embedding,settings
        self.top_k,self.rrf_k=top_k,rrf_k
    def retrieve(self,claim,product_id,snapshot_id,supplement=False,*,prefer_features=False,max_characters=float('inf')):
        refs = self._retrieve(claim, product_id, snapshot_id, supplement, prefer_features)
        if not prefer_features:
            return refs
        limit = self.top_k * (2 if supplement else 1)
        return select_feature_candidates([r for r in refs if r.document_type == 'feature_list'],
            [r for r in refs if r.document_type != 'feature_list'], limit, max_characters, lambda ref: len(ref.quote))

    def _retrieve(self,claim,product_id,snapshot_id,supplement,prefer_features):
        snapshot=self.repo.get('knowledge_snapshot',snapshot_id)
        if snapshot['product_id']!=product_id:raise DomainError('PRODUCT_CONFLICT','知识快照与任务产品不一致',422)
        children=snapshot.get('child_snapshot_ids')
        if children:
            pools=[self._retrieve(claim,product_id,child,supplement,prefer_features) for child in children]
            refs={r.chunk_id:r for pool in pools for r in pool}
            limit = self.top_k * (2 if supplement else 1)
            if prefer_features:
                feature, other = (
                    rrf([[r.chunk_id for r in pool if (r.document_type == 'feature_list') == is_feature]
                         for pool in pools], self.rrf_k) for is_feature in (True, False))
                return [refs[i] for i in feature[:limit] + other[:limit]]
            return [refs[i] for i in rrf([[r.chunk_id for r in pool] for pool in pools],self.rrf_k)][:limit]
        embedding=ProfileEmbedding(snapshot['embedding_profile'],self.settings) if snapshot.get('embedding_profile') else self.embedding
        return HybridRetriever(self.repo,self.knowledge,embedding,self.top_k,self.rrf_k).retrieve(
            claim,product_id,snapshot_id,supplement,prefer_features=prefer_features,collect_pools=prefer_features)
