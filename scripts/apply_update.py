"""Run outside the old launcher's Job, using the newly staged Python runtime."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.update_core import UpdateError, _json, _manifest, install_staged, local_path, safe_child
import psutil


def _request_structure(path, request):
    if request.get('schema_version') != 1:
        raise UpdateError('INVALID_REQUEST', '不支持此更新请求格式。')
    target = local_path(request.get('target_root', ''), exists=True)
    source = local_path(request.get('source_root', ''), exists=True)
    cache = safe_child(target, '.laura-updates')
    if path.name != 'request.json' or path.parent.parent != cache:
        raise UpdateError('INVALID_REQUEST', '更新请求必须位于安装目录的独立更新缓存中。')
    if not source.is_relative_to(path.parent) or source == path.parent:
        raise UpdateError('INVALID_REQUEST', '解压目录必须属于此更新请求缓存。')
    if type(request.get('restart', False)) is not bool or not isinstance(request.get('wait_processes', []), list):
        raise UpdateError('INVALID_REQUEST', '更新请求参数无效。')
    for item in request.get('wait_processes', []):
        if (not isinstance(item, dict) or type(item.get('pid')) is not int or item['pid'] <= 0
                or type(item.get('created')) not in (int, float) or not math.isfinite(item['created']) or item['created'] <= 0):
            raise UpdateError('INVALID_REQUEST', '待退出进程身份无效。')
    if request.get('data_root'):
        local_path(request['data_root'])
    return target, source


def load_request(path):
    path = local_path(path)
    request = _json(path)
    target, source = _request_structure(path, request)
    new, _ = _manifest(source, verify=True)
    old, _ = _manifest(target)
    if (request.get('expected_version') and old['version'] != request['expected_version']
            or request.get('target_version') and new['version'] != request['target_version']
            or request.get('expected_source_commit') and new.get('source_commit') != request['expected_source_commit']):
        raise UpdateError('VERSION_MISMATCH', '本地或下载版本与更新请求不一致，请重新检查更新。')
    return request


def wait_processes(processes, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        live = False
        for record in processes:
            try:
                process = psutil.Process(record['pid'])
                if abs(process.create_time() - record['created']) < 0.1 and process.is_running():
                    live = True
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
            except psutil.AccessDenied as exc:
                raise UpdateError('PROCESS_CHECK_FAILED', '无法确认旧版程序已经退出。') from exc
        if not live:
            return
        if time.monotonic() >= deadline:
            raise UpdateError('PROCESS_EXIT_TIMEOUT', '等待旧版程序退出超时，更新尚未开始。')
        time.sleep(0.2)


def write_state(path,value):
    path=safe_child(path.parent,path.name)
    temporary=safe_child(path.parent,'state.json.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        try:safe_child(path.parent,temporary.name).unlink(missing_ok=True)
        except (OSError,UpdateError):pass


def relaunch_original(target, data_root):
    subprocess.Popen([str(safe_child(target, 'Laura.exe'))], cwd=target, stdin=subprocess.DEVNULL,
        env={**os.environ, 'SOC_DATA_DIR': str(data_root)},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def notify_failure(result):
    if os.name != 'nt':
        return
    import ctypes
    if result.get('installed'):
        explanation = '新版已安装，但后续操作未完成。'
    elif result.get('rolled_back'):
        explanation = '更新未完成，已自动恢复旧程序、数据库和配置。'
    elif result.get('original_preserved'):
        explanation = '更新未完成，原程序和用户数据已保留。'
    else:
        explanation = '更新未完成，尚未确认可安全重新打开。请查看本地更新记录。'
    if result.get('restarted'):
        explanation += '\n已重新打开 Laura。'
    elif result.get('restart_required'):
        explanation += '\n自动重新打开失败，可稍后双击 Laura.exe。'
    if result.get('recovery_required'):
        explanation += '\n恢复状态不明，请保留更新锁和备份，先检查更新记录。'
    explanation += '\n\n' + result['message'] + '\n\n本地备份（如已生成）及下载文件已保留，不会上传。'
    if result.get('state_path'):
        explanation += '\n更新记录：' + result['state_path']
    ctypes.windll.user32.MessageBoxW(None, explanation, 'Laura 更新未完成', 0x30 | 0x10000)


_PREWRITE_ERRORS = {'UPDATE_BUSY', 'ACTIVE_JOBS', 'INSUFFICIENT_SPACE', 'BACKUP_FAILED',
                    'USER_FILE_CONFLICT', 'DATABASE_CHECK_FAILED'}
_BEFORE_INSTALL_ERRORS = {'VERSION_MISMATCH', 'FILE_HASH_MISMATCH', 'INVALID_MANIFEST',
                          'NOT_PORTABLE', 'INVALID_DIRECTORY'}


def _original_is_safe(request, error_code, *, install_called, waited):
    """Known transaction outcome plus intact installed hashes, never a guess."""
    allowed = error_code == 'UPDATE_ROLLED_BACK' or error_code in _PREWRITE_ERRORS
    allowed = allowed or not install_called and error_code in _BEFORE_INSTALL_ERRORS
    if not request or not allowed:
        return False
    try:
        target = local_path(request['target_root'], exists=True)
        lock = safe_child(target, '.laura-update.lock')
        if lock.exists():
            return False
        # Initial source/version rejection can precede the normal exit wait.
        # A failed wait never starts a replacement launcher or kills a process.
        wait_processes(request.get('wait_processes', []), timeout=0 if waited else 90)
        _manifest(target, verify=True)
        return not safe_child(target, '.laura-update.lock').exists()
    except Exception:
        return False


def main(argv=None, *, notify=None, relaunch=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args(argv)
    state_path = None
    request, installed, install_called, waited = None, False, False, False
    try:
        # Establish only the safe output boundary first so invalid-version and
        # invalid-source requests can still leave an actionable local result.
        request_path = local_path(args.request)
        unvalidated = _json(request_path)
        target = local_path(unvalidated.get('target_root', ''), exists=True)
        cache = safe_child(target, '.laura-updates')
        if request_path.name != 'request.json' or request_path.parent.parent != cache:
            raise UpdateError('INVALID_REQUEST', '更新请求不属于安装目录更新缓存。')
        state_path = safe_child(request_path.parent, 'state.json')
        _request_structure(request_path, unvalidated)
        request = unvalidated
        def progress(value):
            write_state(state_path,{'schema_version': 1, 'status': 'installing', **value})
        # Native readiness acknowledges a safely imported helper and validated
        # request boundary. Hashing all runtime files can take several minutes;
        # do it only after acknowledgement and the old processes have exited.
        progress({'phase': 'waiting', 'message': '正在等待旧版服务和启动器退出', 'completed': 0, 'total': 1})
        wait_processes(request.get('wait_processes', []))
        waited = True
        request = load_request(args.request)  # Full integrity/version check before install.
        install_called = True
        report = install_staged(Path(request['source_root']), Path(request['target_root']), progress=progress,
            restart=False, data_root=request.get('data_root'))
        installed = True
        terminal={**report, 'status': 'completed', 'phase': 'completed', 'completed': 1, 'total': 1,
                  'installed': True, 'rolled_back': False, 'restarted': False,
                  'restart_required': False, 'message': '更新完成'}
        # The restarted API must already see a terminal result, otherwise its
        # launcher can hand the same installing token to a second helper.
        progress(terminal)
        if request.get('restart', False):
            try:
                target=local_path(request['target_root'], exists=True)
                data=local_path(request.get('data_root') or os.environ.get('SOC_DATA_DIR') or target/'data')
                (relaunch or relaunch_original)(target,data)
                terminal.update(restarted=True,restart_required=False)
            except Exception:
                terminal.update(restarted=False,restart_required=True,message='更新已完成，自动重新打开失败，可双击Laura.exe打开')
            progress(terminal)
        return 0
    except Exception as exc:
        error = {'schema_version': 1, 'status': 'failed', 'phase': 'failed', 'code': getattr(exc, 'code', 'UPDATE_FAILED'),
                 'message': getattr(exc, 'message', '更新未完成，请查看本地更新记录。'),
                 'installed': installed, 'rolled_back': getattr(exc, 'code', '') == 'UPDATE_ROLLED_BACK',
                 'restarted': False, 'restart_required': False,
                 'recovery_required': getattr(exc, 'code', '') == 'ROLLBACK_FAILED'}
        error['original_preserved'] = not installed and _original_is_safe(request, error['code'], install_called=install_called, waited=waited)
        if request:
            try:
                if safe_child(local_path(request['target_root']), '.laura-update.lock').exists():
                    error['recovery_required'] = True
            except UpdateError:
                error['recovery_required'] = True
        state_persisted=False
        if state_path is not None:
            error['state_path'] = str(state_path)
            try:
                write_state(state_path,error)
                state_persisted=True
            except (OSError,UpdateError):
                error['state_write_failed'] = True
        # The old API also needs a terminal result before restart, otherwise it
        # can hand the same failed request back to the native host a second time.
        if state_persisted and error['original_preserved'] and request.get('restart', False):
            try:
                target = local_path(request['target_root'], exists=True)
                data = local_path(request.get('data_root') or os.environ.get('SOC_DATA_DIR') or target / 'data')
                (relaunch or relaunch_original)(target, data)
                error['restarted'] = True
            except Exception:
                error['restart_required'] = True
            try:
                write_state(state_path,error)
            except (OSError,UpdateError):
                error['state_write_failed'] = True
        print(json.dumps(error, ensure_ascii=False))
        try:
            (notify or notify_failure)(error)
        except Exception:
            pass  # Notification must not obscure the transaction's real outcome.
        return 1


if __name__ == '__main__':
    sys.exit(main())
