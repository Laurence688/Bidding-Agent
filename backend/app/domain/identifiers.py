import hashlib
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SHANGHAI = timezone(timedelta(hours=8))


def session_name(created_at: str) -> str:
    return datetime.fromisoformat(created_at).astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def content_hash(value) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def file_hash(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()
