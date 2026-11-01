import os
import time
from pathlib import Path
import numpy as np
from app.domain.identifiers import file_hash, content_hash
from app.domain.errors import DomainError

QUERY_INSTRUCTION = 'Instruct: Given a technical requirement, retrieve product documentation that provides evidence for or against it.\nQuery: '


class LocalEmbedding:
    def __init__(self, path, device='cpu', max_length=1024, batch_size=4):
        self.path, self.device = Path(path), device
        self.max_length, self.batch_size = max_length, batch_size
        self._model = None
        required = ['model.safetensors', 'config.json', 'tokenizer.json', 'modules.json', '1_Pooling/config.json']
        if not all((self.path / name).is_file() for name in required):
            raise DomainError('EMBEDDING_FILES_MISSING', '本地 Embedding 文件不完整', 503)
        self.fingerprint = content_hash({'files': {name: file_hash(self.path / name) for name in required},
            'normalization': True, 'instruction': QUERY_INSTRUCTION, 'max_length': max_length,
            'window_pooling': 'token-windows-mean-v1', 'chunking': 'structured-v2.2'})
        self.metrics = {'device': device, 'max_length': max_length, 'batch_size': batch_size, 'calls': 0, 'elapsed_seconds': 0}

    @classmethod
    def from_settings(cls, settings):
        cfg = settings.local
        return cls(cfg['embedding_path'], cfg['embedding_device'], cfg['embedding_max_length'], cfg['embedding_batch_size'])

    def _load(self):
        if self._model is not None:
            return
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from sentence_transformers import SentenceTransformer
        import torch
        if self.device.startswith('cuda') and not torch.cuda.is_available():
            raise DomainError('CUDA_UNAVAILABLE', '指定 GPU 不可用；请显式改为 CPU 或安装匹配依赖', 503)
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self._model = SentenceTransformer(str(self.path), device=self.device, local_files_only=True, trust_remote_code=False)
        self._model.max_seq_length = self.max_length
        self.metrics['actual_dtype'] = str(next(self._model.parameters()).dtype)

    def embed(self, texts, query=False):
        self._load()
        started = time.monotonic()
        tokenizer = self._model.tokenizer
        prompt = QUERY_INSTRUCTION if query else ''
        budget = self.max_length - len(tokenizer.encode(prompt,add_special_tokens=False)) - 32
        if budget < 32:
            raise DomainError('EMBEDDING_WINDOW_INVALID','Embedding 上下文窗口过小',422)
        windows, owners = [], []
        for owner,text in enumerate(texts):
            ids = tokenizer.encode(text,add_special_tokens=False)
            if len(ids) <= budget:
                windows.append(text); owners.append(owner)
                continue
            step = budget - min(64,budget//4)
            for offset in range(0,len(ids),step):
                windows.append(tokenizer.decode(ids[offset:offset+budget],skip_special_tokens=True))
                owners.append(owner)
                if offset+budget >= len(ids):break
        encoded = self._model.encode(windows, prompt=prompt,
            batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        encoded = np.asarray(encoded,dtype='float32')
        result = np.stack([encoded[np.asarray(owners)==owner].mean(axis=0) for owner in range(len(texts))])
        self.metrics['windows'] = self.metrics.get('windows',0) + len(windows)
        self.metrics['calls'] += 1
        self.metrics['elapsed_seconds'] += time.monotonic() - started
        self.metrics['dimensions'] = result.shape[1]
        if self.device.startswith('cuda'):
            import torch
            self.metrics['peak_gpu_bytes'] = torch.cuda.max_memory_allocated()
        # 权重可能默认 bfloat16；转为 float32 后再归一化，避免低精度范数漂移。
        result = np.asarray(result, dtype='float32')
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        if not np.isfinite(result).all() or (norms == 0).any():
            raise DomainError('EMBEDDING_INVALID', '本地模型返回无效向量', 503)
        return result / norms
