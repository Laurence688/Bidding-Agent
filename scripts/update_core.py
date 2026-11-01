"""Local, manifest-authorized portable updates. No network or model calls.

Backups contain private data and stay on this computer. A failed probe restores
both program files and the pre-update SQLite snapshot before reporting failure.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import uuid
import zipfile

import psutil

MAX_FILES = 150_000
MAX_TOTAL_BYTES = 12 * 1024 ** 3
MAX_MANIFEST_BYTES = 64 * 1024 ** 2
MIN_FREE_BYTES = 64 * 1024 ** 2
PROTECTED = {'data', 'models', '.git', '.laura-backups', '.laura-updates', '.laura-update.lock'}


class UpdateError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or '\x00' in name or name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/'))):
        raise UpdateError('UNSAFE_PATH', '更新包包含不安全路径。')
    path = PurePosixPath(name)
    for part in path.parts:
        if (part.endswith((' ', '.')) or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(n) for n in range(1, 10)), *('LPT'+str(n) for n in range(1, 10))}):
            raise UpdateError('UNSAFE_PATH', '更新包包含 Windows 不安全路径。')
    return path


def _program_path(name):
    path = _relative(name)
    parts = tuple(p.casefold() for p in path.parts)
    if (parts[0] in PROTECTED or parts[0].startswith('.env') and parts[0] != '.env.example'
            or parts[0] == 'config' and (len(parts) != 2 or not parts[1].endswith('.example.yaml'))):
        raise UpdateError('USER_STATE_IN_PACKAGE', '更新清单不能覆盖用户数据、模型、密钥或实际配置。')
    return path


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def local_path(value, *, exists=False):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise UpdateError('UNSAFE_PATH', '更新目录必须是本机绝对路径。')
    # Reject even links that currently resolve inside the installation: a junction
    # can be retargeted and must never become a recursive-delete boundary.
    for current in [*reversed(path.parents), path]:
        if current.exists() and _is_link(current):
            raise UpdateError('UNSAFE_PATH', '更新目录不能经过符号链接或目录联接。')
    path = path.resolve()
    if exists and not path.is_dir():
        raise UpdateError('INVALID_DIRECTORY', '更新目录不存在。')
    return path


def safe_child(root, name):
    relative = _relative(name)
    target = local_path(root.joinpath(*relative.parts))
    if target == root or not target.is_relative_to(root):
        raise UpdateError('UNSAFE_PATH', '更新操作越过了指定目录。')
    return target


def _json(path):
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError('oversized metadata')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON property')
                result[key] = value
            return result
        result = json.loads(path.read_text('utf-8'), object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError('expected JSON object')
        return result
    except (OSError, UnicodeError, ValueError) as exc:
        raise UpdateError('INVALID_MANIFEST', '更新清单无效或无法读取。') from exc


def _manifest(root, *, verify=False):
    root = local_path(root, exists=True)
    if (root / '.git').exists():
        raise UpdateError('DEVELOPMENT_DIRECTORY', '开发目录不能使用便携版自动更新。')
    for name in ('laura-bundle.json', 'package-manifest.json'):
        if not safe_child(root, name).is_file():
            raise UpdateError('NOT_PORTABLE', '仅支持包含版本清单的 Laura 便携安装目录。')
    bundle = _json(root / 'laura-bundle.json')
    manifest = _json(root / 'package-manifest.json')
    if bundle.get('schema_version') != 1 or manifest.get('schema_version') != 1 or not isinstance(manifest.get('version'), str):
        raise UpdateError('INVALID_MANIFEST', '不支持此便携包清单格式。')
    entries, seen, total = {}, set(), 0
    if not isinstance(manifest.get('files'), list) or len(manifest['files']) > MAX_FILES:
        raise UpdateError('INVALID_MANIFEST', '更新文件清单无效或超过数量限制。')
    for entry in manifest['files']:
        if not isinstance(entry, dict):
            raise UpdateError('INVALID_MANIFEST', '更新文件条目无效。')
        name = entry.get('path')
        _program_path(name)
        key = name.casefold()
        if key in seen or key == 'package-manifest.json':
            raise UpdateError('INVALID_MANIFEST', '更新清单存在重复文件。')
        seen.add(key)
        size, checksum = entry.get('size'), entry.get('sha256')
        if type(size) is not int or size < 0 or not isinstance(checksum, str) or not re.fullmatch('[0-9a-fA-F]{64}', checksum):
            raise UpdateError('INVALID_MANIFEST', '更新文件大小或 SHA256 无效。')
        total += size
        if total > MAX_TOTAL_BYTES:
            raise UpdateError('PACKAGE_LIMIT', '更新包超过解压大小限制。')
        entries[name] = {**entry, 'sha256': checksum.lower()}
        if verify:
            file = safe_child(root, name)
            if not file.is_file() or file.stat().st_size != size or digest(file) != checksum.lower():
                raise UpdateError('FILE_HASH_MISMATCH', '更新文件 SHA256 验证失败：' + name)
    if 'laura-bundle.json' not in entries:
        raise UpdateError('INVALID_MANIFEST', '便携标识未在更新清单中登记。')
    # File/directory aliases and spelling conflicts are unsafe on Windows.
    prefixes = {}
    for name in entries:
        parts = name.split('/')
        for length in range(1, len(parts)):
            prefix = '/'.join(parts[:length])
            key = prefix.casefold()
            if key in seen or key in prefixes and prefixes[key] != prefix:
                raise UpdateError('INVALID_MANIFEST', '更新清单存在文件目录冲突。')
            prefixes[key] = prefix
    return manifest, entries


def extract_verified_zip(archive: Path, staging: Path, expected_sha256: str) -> Path:
    archive = local_path(archive)
    staging = local_path(staging)
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-fA-F]{64}', expected_sha256) or digest(archive) != expected_sha256.lower():
        raise UpdateError('ARCHIVE_HASH_MISMATCH', '下载 ZIP 的 SHA256 校验失败。')
    if staging.exists():
        raise UpdateError('STAGING_EXISTS', '请使用新的更新解压目录。')
    try:
        with zipfile.ZipFile(archive) as package:
            infos = package.infolist()
            if len(infos) > MAX_FILES + 1000 or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES:
                raise UpdateError('PACKAGE_LIMIT', '更新包超过文件数量或大小限制。')
            seen, files, directories = set(), {}, set()
            for item in infos:
                name = item.filename[:-1] if item.is_dir() else item.filename
                path = _relative(name)
                if path.parts[0] != 'Laura' or len(path.parts) < 2 and not item.is_dir():
                    raise UpdateError('INVALID_ZIP_LAYOUT', 'ZIP 必须只有一个 Laura 根目录。')
                key = name.casefold()
                mode = item.external_attr >> 16
                if key in seen or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or item.flag_bits & 1:
                    raise UpdateError('UNSAFE_ZIP', 'ZIP 包含重复路径、链接或不支持的条目。')
                seen.add(key)
                if item.is_dir():
                    directories.add(name)
                else:
                    relative = '/'.join(path.parts[1:])
                    if relative != 'package-manifest.json':
                        _program_path(relative)
                    files[relative] = item
            metadata = files.get('package-manifest.json')
            if metadata is None or metadata.file_size > MAX_MANIFEST_BYTES:
                raise UpdateError('INVALID_MANIFEST', 'ZIP 缺少有效更新清单。')
            # Materialize metadata first to use the same strict parser as install.
            staging.mkdir(parents=True)
            root = safe_child(staging, 'Laura')
            root.mkdir()
            for name in ('package-manifest.json', 'laura-bundle.json'):
                item = files.get(name)
                if item is None or item.file_size > MAX_MANIFEST_BYTES:
                    raise UpdateError('INVALID_MANIFEST', 'ZIP 缺少便携标识。')
                (root / name).write_bytes(package.read(item))
            _, entries = _manifest(root)
            if set(files) != set(entries) | {'package-manifest.json'}:
                raise UpdateError('MANIFEST_ZIP_MISMATCH', 'ZIP 文件与清单不一致。')
            allowed_directories = {'Laura'}
            for name in files:
                parts = ('Laura/' + name).split('/')
                allowed_directories.update('/'.join(parts[:n]) for n in range(1, len(parts)))
            if not directories.issubset(allowed_directories):
                raise UpdateError('MANIFEST_ZIP_MISMATCH', 'ZIP 包含未登记或大小写冲突的目录。')
            for name, entry in entries.items():
                item = files[name]
                if item.file_size != entry['size']:
                    raise UpdateError('FILE_HASH_MISMATCH', 'ZIP 文件大小不符：' + name)
                destination = safe_child(root, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                sha, written = hashlib.sha256(), 0
                with package.open(item) as incoming, destination.open('wb') as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        written += len(chunk)
                        if written > entry['size']:
                            raise UpdateError('PACKAGE_LIMIT', '解压文件超过声明大小。')
                        sha.update(chunk)
                        outgoing.write(chunk)
                if sha.hexdigest() != entry['sha256'] or written != entry['size']:
                    raise UpdateError('FILE_HASH_MISMATCH', '解压文件 SHA256 校验失败：' + name)
            return root
    except UpdateError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError('EXTRACTION_FAILED', '更新包解压失败。') from exc


def assert_quiescent(target, data_root):
    """Read only: never initialize Repository (which would migrate before backup)."""
    runtime = safe_child(data_root, 'runtime')
    if runtime.exists():
        for name in ('api', 'worker', 'supervisor', 'browser-launcher', 'desktop-launcher'):
            record_file = safe_child(runtime, name + '.json')
            if not record_file.exists():
                continue
            try:
                record = _json(record_file)
                created = record.get('started', record.get('created', record.get('create_time')))
                process = psutil.Process(int(record['pid']))
                if created is not None and abs(process.create_time() - float(created)) < 0.1 and process.is_running():
                    raise UpdateError('SERVICES_RUNNING', 'Laura 服务仍在运行，请等待程序关闭后更新。')
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, KeyError, TypeError, ValueError) as exc:
                raise UpdateError('PROCESS_CHECK_FAILED', '无法确认本地服务已经停止。') from exc
    db_path = safe_child(data_root, 'app.db')
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
                active = conn.execute("SELECT 1 FROM records WHERE deleted=0 AND kind IN ('job','kb_build') AND json_extract(data,'$.status') IN ('queued','running','cancelling','building') LIMIT 1").fetchone()
                if active:
                    raise UpdateError('ACTIVE_JOBS', '存在排队、运行或取消中的任务（含知识库构建），请完成或取消后更新。')
    except sqlite3.Error as exc:
        raise UpdateError('DATABASE_CHECK_FAILED', '无法安全检查任务状态，更新尚未开始。') from exc


def _tree_files(root):
    if not root.exists():
        return []
    result = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = local_path(Path(folder) / name)
            if path.is_file():
                result.append(path)
    return result


def _copy_file(source, target):
    source, target = local_path(source), local_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _replace_program(source, target):
    """Copy into a sibling first: a locked destination keeps its old bytes."""
    source, target = local_path(source), local_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_child(target.parent, target.name + '.laura-update-' + uuid.uuid4().hex + '.tmp')
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        shutil.copystat(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_directories(source, target):
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for folder, directories, _ in os.walk(source, followlinks=False):
        for name in directories:
            directory = local_path(Path(folder) / name)
            safe_child(target, directory.relative_to(source).as_posix()).mkdir(parents=True, exist_ok=True)


def _snapshot_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise UpdateError('BACKUP_FAILED', '数据库备份超时，更新尚未开始。')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as incoming:
        with closing(sqlite3.connect(target)) as outgoing:
            incoming.backup(outgoing, pages=256, progress=progress, sleep=0.1)
            if outgoing.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise UpdateError('BACKUP_FAILED', '数据库备份完整性检查失败。')


def _remove_tree(root):
    root = local_path(root)
    if root.exists():
        _tree_files(root)  # Fail before recursively deleting any linked directory.
        shutil.rmtree(root)


def _restore_state(target, data_root, backup, state):
    # Restore contents through the same checked absolute boundaries. The data
    # directory itself may be an explicitly configured local external directory.
    _remove_tree(data_root)
    if state['data_existed']:
        shutil.copytree(safe_child(backup, 'state/data'), data_root)
    config = safe_child(target, 'config')
    _remove_tree(config)
    if state['config_existed']:
        shutil.copytree(safe_child(backup, 'state/config'), config)
    env = safe_child(target, '.env.local')
    if env.exists():
        env.unlink()
    if state['env_existed']:
        _copy_file(safe_child(backup, 'state/.env.local'), env)


def run_probe(target, data_root=None):
    executable = safe_child(target, '.runtimes/python/python.exe')
    script = safe_child(target, 'scripts/update_probe.py')
    if not executable.is_file() or not script.is_file():
        raise UpdateError('PROBE_UNAVAILABLE', '新版缺少独立更新检查程序。')
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON') and k != 'SOC_PACKAGED'}
    env.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', SOC_DATA_DIR=str(data_root or target / 'data'))
    result = subprocess.run([str(executable), str(script), '--root', str(target), '--data-root', env['SOC_DATA_DIR']],
        cwd=target, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        # Never propagate stdout/configuration content (which may contain keys).
        raise UpdateError('PROBE_FAILED', '新版依赖或数据库迁移检查失败。')
    return True


@contextmanager
def _install_lock(target):
    path = safe_child(target, '.laura-update.lock')
    recovery_required = False
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UpdateError('UPDATE_LOCKED', '已有更新事务或未完成的更新锁，请先检查本地更新记录。') from exc
    try:
        with os.fdopen(handle, 'w') as file:
            file.write(json.dumps({'pid': os.getpid(), 'created': psutil.Process().create_time()}))
        yield
    except UpdateError as exc:
        recovery_required = exc.code == 'ROLLBACK_FAILED'
        raise
    finally:
        # A failed restore leaves an unsafe mixed installation. Keep the durable
        # launch guard until its local backup/transaction has been recovered.
        if not recovery_required:
            safe_child(target, '.laura-update.lock').unlink(missing_ok=True)


def install_staged(source_root, target_root, *, progress=None, probe=None, restart=False, data_root=None) -> dict:
    target = local_path(target_root, exists=True)
    source = local_path(source_root, exists=True)
    if source == target or target.is_relative_to(source):
        raise UpdateError('INVALID_DIRECTORY', '更新源和安装目录必须相互独立。')
    data = local_path(data_root or os.environ.get('SOC_DATA_DIR') or target / 'data')
    if (data == target or target.is_relative_to(data) or data.is_relative_to(source)
            or data.is_relative_to(target) and data != target / 'data'):
        raise UpdateError('UNSAFE_DATA_DIRECTORY', '数据目录与更新程序目录重叠，无法安全自动备份。')
    new_manifest, new = _manifest(source, verify=True)
    manifest_hash = digest(safe_child(source, 'package-manifest.json'))
    old_manifest, old = _manifest(target)
    def emit(phase, completed=0, total=1, message=''):
        if progress:
            progress({'phase': phase, 'completed': completed, 'total': total, 'message': message})
    with _install_lock(target):
        emit('checking', message='正在确认本地任务和服务已停止')
        assert_quiescent(target, data)
        state = {'data_existed': data.exists(), 'config_existed': (target / 'config').exists(), 'env_existed': (target / '.env.local').exists()}
        data_files = _tree_files(data)
        config_files = _tree_files(safe_child(target, 'config'))
        if state['env_existed']:
            local_path(target / '.env.local')
        changed, obsolete = [], []
        old_folded = {name.casefold(): name for name in old}
        if any(name.casefold() in old_folded and old_folded[name.casefold()] != name for name in new):
            raise UpdateError('INVALID_MANIFEST', '跨版本文件路径大小写变化需要重新安装。')
        for name, entry in new.items():
            dst = safe_child(target, name)
            same = dst.is_file() and dst.stat().st_size == entry['size'] and digest(dst) == entry['sha256']
            if dst.exists() and not dst.is_file():
                raise UpdateError('USER_FILE_CONFLICT', '目标目录存在同名用户文件夹：' + name)
            if name not in old and dst.exists() and not same:
                raise UpdateError('USER_FILE_CONFLICT', '新版程序与现有用户文件冲突：' + name)
            if not same:
                changed.append(name)
        for name in old.keys() - new.keys():
            if safe_child(target, name).is_file():
                obsolete.append(name)
        program_names = [*changed, *obsolete, 'package-manifest.json']
        original = [name for name in program_names if safe_child(target, name).is_file()]
        required = sum(f.stat().st_size for f in data_files + config_files)
        required += sum(safe_child(target, name).stat().st_size for name in original)
        required += sum(new[name]['size'] for name in changed) + MIN_FREE_BYTES
        if shutil.disk_usage(target).free < required:
            raise UpdateError('INSUFFICIENT_SPACE', '本机空间不足，无法完成备份与更新；程序尚未替换。')
        version = re.sub(r'[^A-Za-z0-9._-]', '_', old_manifest['version'])[:60]
        backup = safe_child(target, '.laura-backups/' + version + '-' + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        emit('backing_up', message='正在自动备份数据、会话、记忆、密钥和旧程序')
        try:
            state_data = safe_child(backup, 'state/data')
            if state['data_existed']:
                state_data.mkdir(parents=True)
                _copy_directories(data, state_data)
            for file in data_files:
                name = file.relative_to(data).as_posix()
                if name in {'app.db-wal', 'app.db-shm'}:
                    continue
                dst = safe_child(state_data, name)
                if name == 'app.db':
                    _snapshot_database(file, dst)
                else:
                    _copy_file(file, dst)
            for file in config_files:
                _copy_file(file, safe_child(backup, 'state/config/' + file.relative_to(target / 'config').as_posix()))
            if state['config_existed']:
                safe_child(backup, 'state/config').mkdir(parents=True, exist_ok=True)
                _copy_directories(target / 'config', safe_child(backup, 'state/config'))
            if state['env_existed']:
                _copy_file(target / '.env.local', safe_child(backup, 'state/.env.local'))
            for name in original:
                _copy_file(safe_child(target, name), safe_child(backup, 'program/' + name))
            safe_child(backup, 'transaction.json').write_text(json.dumps({'schema_version': 1, 'old_version': old_manifest['version'], 'new_version': new_manifest['version'], 'data_root': str(data), 'program_files': program_names, 'original_files': original, **state}, ensure_ascii=False), 'utf-8')
        except Exception as exc:
            raise UpdateError('BACKUP_FAILED', '自动备份失败，程序尚未替换。') from exc
        # Second read closes the time spent backing up. Launcher/service shutdown
        # plus this check is required; users must not concurrently reopen Laura.
        assert_quiescent(target, data)
        try:
            for index, name in enumerate(changed):
                emit('installing', index, len(changed), '正在替换程序：' + name)
                destination = safe_child(target, name)
                # Reverify staged content immediately before copying.
                src = safe_child(source, name)
                if digest(src) != new[name]['sha256']:
                    raise UpdateError('FILE_HASH_MISMATCH', '安装源在校验后发生变化。')
                _replace_program(src, destination)
                if digest(destination) != new[name]['sha256']:
                    raise UpdateError('FILE_HASH_MISMATCH', '复制后的程序校验失败。')
            for name in obsolete:
                safe_child(target, name).unlink()
            if digest(safe_child(source, 'package-manifest.json')) != manifest_hash:
                raise UpdateError('FILE_HASH_MISMATCH', '安装清单在校验后发生变化。')
            _replace_program(safe_child(source, 'package-manifest.json'), safe_child(target, 'package-manifest.json'))
            emit('probing', message='正在验证依赖、数据库迁移和前端资源')
            if (probe(target) if probe else run_probe(target, data)) is False:
                raise UpdateError('PROBE_FAILED', '新版更新检查未通过。')
        except Exception as exc:
            emit('rolling_back', message='更新未通过，正在自动恢复旧程序和数据')
            try:
                for name in program_names:
                    destination = safe_child(target, name)
                    if name in original:
                        previous = safe_child(backup, 'program/' + name)
                        if not destination.is_file() or digest(destination) != digest(previous):
                            _replace_program(previous, destination)
                    elif destination.exists():
                        destination.unlink()
                _restore_state(target, data, backup, state)
            except Exception as rollback_exc:
                raise UpdateError('ROLLBACK_FAILED', '更新失败且自动恢复未完成。请保留本地备份并查看更新记录：' + str(backup)) from rollback_exc
            raise UpdateError('UPDATE_ROLLED_BACK', '更新失败，已自动恢复旧程序、数据库和配置。下载文件已保留，可重试。') from exc
        report = {'status': 'completed', 'version': new_manifest['version'], 'previous_version': old_manifest['version'], 'backup_path': str(backup), 'changed_files': len(changed), 'removed_files': len(obsolete), 'data_root': str(data)}
        emit('completed', 1, 1, '更新完成，用户数据和设置已保留')
    # Release the transaction lock before the new launcher checks it on startup.
    if restart:
        try:
            subprocess.Popen([str(safe_child(target, 'Laura.exe'))], cwd=target, stdin=subprocess.DEVNULL,
                env={**os.environ, 'SOC_DATA_DIR': str(data)},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError:
            report['restart_required'] = True
    return report
