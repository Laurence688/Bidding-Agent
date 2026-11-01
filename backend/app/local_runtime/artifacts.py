"""Fail-closed artifact validation. A matching hash is integrity, not quality acceptance."""
import hashlib
import re
import struct
from pathlib import Path, PureWindowsPath

from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, file_hash


def fail(code, message):
    raise DomainError(code, message, 422)


def inspect_gguf(path):
    """Read bounded GGUF v3 metadata without loading tensors or model code."""
    with Path(path).open('rb') as stream:
        def read(size):
            if size < 0 or size > 64 * 1024 * 1024 or stream.tell() + size > 256 * 1024 * 1024:
                fail('ARTIFACT_GGUF_INVALID', 'GGUF 元数据超出安全读取范围')
            data = stream.read(size)
            if len(data) != size:
                fail('ARTIFACT_GGUF_INVALID', 'GGUF 元数据不完整')
            return data

        def unpack(fmt):
            return struct.unpack('<' + fmt, read(struct.calcsize('<' + fmt)))[0]

        def string():
            try:
                return read(unpack('Q')).decode('utf-8')
            except UnicodeDecodeError:
                fail('ARTIFACT_GGUF_INVALID', 'GGUF 字符编码无效')

        formats = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f', 7: '?', 10: 'Q', 11: 'q', 12: 'd'}

        def value(kind, depth=0):
            if kind == 8:
                return string()
            if kind == 9:
                subtype, count = unpack('I'), unpack('Q')
                if depth or count > 2_000_000:
                    fail('ARTIFACT_GGUF_INVALID', 'GGUF 元数据数组无效')
                return [value(subtype, depth + 1) for _ in range(count)]
            if kind not in formats:
                fail('ARTIFACT_GGUF_INVALID', 'GGUF 元数据类型不支持')
            return unpack(formats[kind])

        if read(4) != b'GGUF' or unpack('I') != 3:
            fail('ARTIFACT_GGUF_INVALID', '仅支持 GGUF v3 工件')
        tensor_count, count = unpack('Q'), unpack('Q')
        if count > 10000 or tensor_count > 100000:
            fail('ARTIFACT_GGUF_INVALID', 'GGUF 计数无效')
        metadata = {}
        for _ in range(count):
            key = string()
            if key in metadata:
                fail('ARTIFACT_GGUF_INVALID', 'GGUF 元数据键重复')
            metadata[key] = value(unpack('I'))
    tokenizer = {k: v for k, v in metadata.items() if k.startswith('tokenizer.') and k != 'tokenizer.chat_template'}
    template = metadata.get('tokenizer.chat_template')
    if not tokenizer or not isinstance(template, str) or not template:
        fail('ARTIFACT_GGUF_INVALID', '工件缺少 tokenizer 或 chat template')
    return {'architecture': metadata.get('general.architecture'), 'file_type': metadata.get('general.file_type'),
            'tokenizer_sha256': content_hash(tokenizer), 'chat_template_sha256': hashlib.sha256(template.encode()).hexdigest(),
            'chat_template': template, 'tensor_count': tensor_count}


def validate_artifact(manifest, model_root):
    root = Path(model_root).resolve()
    sha = re.compile(r'[0-9a-f]{64}')
    revision = re.compile(r'[0-9a-f]{40}')
    def valid_revision(source):
        return isinstance(source, dict) and bool(revision.fullmatch(str(source.get('revision', ''))))
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
        or not manifest.get('model_id') or manifest.get('quantization') != 'Q4_K_M'
        or not valid_revision(manifest.get('upstream')) or not valid_revision(manifest.get('conversion'))
        or not manifest.get('runtime_version') or 'latest' in str(manifest['runtime_version']).lower()
        or not sha.fullmatch(str(manifest.get('tokenizer_sha256', '')))
        or not sha.fullmatch(str(manifest.get('chat_template_sha256', '')))):
        fail('ARTIFACT_MANIFEST_INVALID', '模型清单缺少固定版本、哈希或来源')
    files = manifest.get('files', [])
    if (not isinstance(files, list) or not files or any(not isinstance(f, dict) for f in files)
        or sum(f.get('role') == 'model' for f in files) != 1
        or sum(f.get('role') == 'runtime' for f in files) != 1
        or not any(f.get('role') == 'license' for f in files)):
        fail('ARTIFACT_MANIFEST_INVALID', '清单必须包含生成模型、运行时及许可')
    resolved, seen = {}, set()
    for item in files:
        name = item.get('path')
        if not isinstance(name, str) or not name or PureWindowsPath(name).is_absolute() or PureWindowsPath(name).drive or ':' in name:
            fail('ARTIFACT_PATH_INVALID', '模型文件路径必须位于模型目录内')
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root or path in seen:
            fail('ARTIFACT_PATH_INVALID', '模型文件路径越界或重复')
        seen.add(path)
        if not path.is_file():
            fail('ARTIFACT_MISSING', '模型工件文件缺失：' + name)
        if not sha.fullmatch(str(item.get('sha256', ''))) or not isinstance(item.get('size'), int) or item['size'] <= 0:
            fail('ARTIFACT_MANIFEST_INVALID', '模型工件文件缺少合法哈希或体积')
        if path.stat().st_size != item['size'] or file_hash(path) != item['sha256']:
            fail('ARTIFACT_HASH_MISMATCH', '模型工件校验失败：' + name)
        if item.get('role') in {'model', 'runtime'}:
            resolved[item['role'] + '_path'] = path
    with resolved['runtime_path'].open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            fail('ARTIFACT_RUNTIME_INVALID', '运行时不是 Windows 可执行文件')
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset > 16 * 1024 * 1024:
            fail('ARTIFACT_RUNTIME_INVALID', '运行时 PE 头无效')
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0\x64\x86':
            fail('ARTIFACT_RUNTIME_INVALID', '运行时必须为 Windows x64')
    metadata = inspect_gguf(resolved['model_path'])
    if metadata['architecture'] != manifest.get('architecture'):
        fail('ARTIFACT_ARCHITECTURE_MISMATCH', 'GGUF 架构与清单不匹配')
    if metadata['file_type'] != 15:
        fail('ARTIFACT_QUANTIZATION_MISMATCH', 'GGUF 量化类型不是 Q4_K_M')
    if metadata['tokenizer_sha256'] != manifest['tokenizer_sha256']:
        fail('ARTIFACT_TOKENIZER_MISMATCH', 'GGUF tokenizer 与清单不匹配')
    if metadata['chat_template_sha256'] != manifest['chat_template_sha256']:
        fail('ARTIFACT_TEMPLATE_MISMATCH', 'GGUF chat template 与清单不匹配')
    return {**resolved, 'identity': content_hash(manifest), 'metadata': metadata}
