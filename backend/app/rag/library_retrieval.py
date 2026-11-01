from app.rag.retrieval import HybridRetriever
from app.rag.indexes import rrf
from app.services.libraries import ProfileEmbedding
from app.domain.errors import DomainError

class LibraryRetriever:
    """Each immutable child index is queried with its own frozen model profile."""
    def __init__(self,repo,knowledge,embedding,settings,top_k=8,rrf_k=60):
        self.repo,self.knowledge,self.embedding,self.settings=repo,knowledge,embedding,settings
        self.top_k,self.rrf_k=top_k,rrf_k
    def retrieve(self,claim,product_id,snapshot_id,supplement=False):
        snapshot=self.repo.get('knowledge_snapshot',snapshot_id)
        if snapshot['product_id']!=product_id:raise DomainError('PRODUCT_CONFLICT','知识快照与任务产品不一致',422)
        children=snapshot.get('child_snapshot_ids')
        if children:
            pools=[self.retrieve(claim,product_id,child,supplement) for child in children]
            refs={r.chunk_id:r for pool in pools for r in pool}
            return [refs[i] for i in rrf([[r.chunk_id for r in pool] for pool in pools],self.rrf_k)][:self.top_k*(2 if supplement else 1)]
        embedding=ProfileEmbedding(snapshot['embedding_profile'],self.settings) if snapshot.get('embedding_profile') else self.embedding
        return HybridRetriever(self.repo,self.knowledge,embedding,self.top_k,self.rrf_k).retrieve(claim,product_id,snapshot_id,supplement)
