import re
import faiss
import numpy as np


def lexical_tokens(text):
    words = re.findall(r'[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*|[\u4e00-\u9fff]', text.lower())
    chinese = re.findall(r'[\u4e00-\u9fff]+', text)
    return ' '.join(words + exact_terms(text) + [phrase[i:i+2] for phrase in chinese for i in range(len(phrase)-1)])


def exact_terms(text):
    return list(dict.fromkeys('rfc'+n for n in re.findall(r'\bRFC[\s-]*(\d+)\b', text, re.I)))


def build_vectors(vectors):
    vectors = np.asarray(vectors, dtype='float32').copy()
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def rrf(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, ident in enumerate(ranking, 1):
            scores[ident] = scores.get(ident, 0) + 1 / (k + rank)
    lexical_order = {ident:rank for rank,ident in enumerate(rankings[0] if rankings else [])}
    return sorted(scores, key=lambda key: (-scores[key], lexical_order.get(key,float('inf')), key))
