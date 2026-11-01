"""Bounded process caches with active leases and an interprocess heavy-work lock."""
import json
import os
import threading
import time
import weakref
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, new_id
from app.security.configuration import configuration_lock, atomic_write

_heavy_local = threading.local()
_pools = weakref.WeakSet()
_janitor_lock = threading.Lock()
_janitor_started = False


def _register_pool(pool):
    global _janitor_started
    with _janitor_lock:
        _pools.add(pool)
        if _janitor_started:
            return
        _janitor_started = True
    def sweep():
        while True:
            time.sleep(30)
            with _janitor_lock:
                pools = list(_pools)
            for item in pools:
                item.expire()
    threading.Thread(target=sweep, daemon=True, name='laura-resource-expiry').start()


class ResourcePool:
    def __init__(self, max_entries=2, max_bytes=512 * 1024 * 1024, idle_seconds=600, clock=time.monotonic):
        self.max_entries, self.max_bytes, self.idle_seconds, self.clock = max_entries, max_bytes, idle_seconds, clock
        self.entries, self.lock = {}, threading.RLock()
        self.hits = self.misses = self.active = 0
        _register_pool(self)

    def expire(self):
        with self.lock:
            for key, entry in list(self.entries.items()):
                if not entry['active'] and self.clock() - entry['used'] >= self.idle_seconds:
                    del self.entries[key]

    @contextmanager
    def acquire(self, key, loader, size_bytes=0):
        with self.lock:
            self.expire()
            entry = self.entries.get(key)
            if entry is not None:
                self.hits += 1
            else:
                self.misses += 1
                entry = {'value': loader(), 'bytes': size_bytes, 'used': self.clock(), 'active': 0}
                while (len(self.entries) >= self.max_entries or sum(e['bytes'] for e in self.entries.values()) + size_bytes > self.max_bytes):
                    idle = [(e['used'], k) for k, e in self.entries.items() if not e['active']]
                    if not idle:
                        break
                    del self.entries[min(idle, key=lambda item: item[0])[1]]
                if len(self.entries) < self.max_entries and sum(e['bytes'] for e in self.entries.values()) + size_bytes <= self.max_bytes:
                    self.entries[key] = entry
            entry['active'] += 1
            self.active += 1
        try:
            yield entry['value']
        finally:
            with self.lock:
                entry['active'] -= 1
                entry['used'] = self.clock()
                self.active -= 1

    def status(self):
        with self.lock:
            self.expire()
            return {'entries': len(self.entries), 'bytes': sum(e['bytes'] for e in self.entries.values()),
                    'active': self.active, 'hits': self.hits, 'misses': self.misses}


@lru_cache(maxsize=8)
def index_pool(data_dir):
    return ResourcePool()


@lru_cache(maxsize=8)
def embedding_pool(data_dir):
    return ResourcePool(max_entries=1, max_bytes=2 * 1024 ** 3)


def configure_pools(data_dir, options):
    indexes, embedding = index_pool(str(data_dir)), embedding_pool(str(data_dir))
    entries = options.get('index_cache_entries', 2)
    size = options.get('index_cache_bytes', 512 * 1024 ** 2)
    idle = options.get('idle_seconds', 600)
    if any(type(v) is not int for v in (entries,size,idle)) or not 0 <= entries <= 8 or not 0 <= size <= 2 * 1024 ** 3 or not 1 <= idle <= 86400:
        raise DomainError('RESOURCE_CONFIG_INVALID','本地缓存参数无效',422)
    with indexes.lock:
        indexes.max_entries,indexes.max_bytes,indexes.idle_seconds=entries,size,idle
    with embedding.lock:
        embedding.idle_seconds=idle


def local_model_key(profile):
    path = Path(profile['model']).resolve()
    required = ['model.safetensors', 'config.json', 'tokenizer.json', 'modules.json', '1_Pooling/config.json']
    files = {name: (p.stat().st_size, p.stat().st_mtime_ns) if (p := path / name).is_file() else None for name in required}
    return content_hash({'profile': profile, 'files': files}), sum(v[0] for v in files.values() if v)


@contextmanager
def heavy_lease(data_dir, purpose, timeout_seconds=30):
    root = Path(data_dir) / 'runtime' / 'heavy'
    try:
        with configuration_lock(root, timeout_seconds=timeout_seconds):
            key = str(root.resolve())
            depths = getattr(_heavy_local, 'depths', {})
            outer = not depths.get(key, 0)
            depths[key] = depths.get(key, 0) + 1
            _heavy_local.depths = depths
            path = root / 'lease.json'
            if outer:
                atomic_write(path, json.dumps({'id': new_id(), 'pid': os.getpid(), 'purpose': purpose, 'started_at': time.time()}))
            try:
                yield
            finally:
                depths[key] -= 1
                if outer:
                    path.unlink(missing_ok=True)
    except DomainError as exc:
        if exc.code == 'CONFIG_BUSY':
            raise DomainError('RESOURCE_BUSY', '本机正在进行生成或向量计算，请稍后重试；未发送或截断输入', 409) from exc
        raise
