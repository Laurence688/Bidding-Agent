"""Starts only this workspace's API and worker, without visible console windows."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import psutil
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings

def owned_process(folder, role):
    try:
        record = json.loads((folder / f'{role}.json').read_text('utf-8'))
        process = psutil.Process(record['pid'])
        args = process.cmdline()
        if (record['root'] == str(ROOT) and record['role'] == role
                and abs(process.create_time() - record['started']) < 0.1
                and str(ROOT / 'scripts' / 'service_entry.py') in args and role in args):
            return process
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    return None

def api_health(port=8765, instance_id=None):
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            token = client.get(f'http://127.0.0.1:{port}/api/v1/bootstrap').json()['token']
            result = client.get(f'http://127.0.0.1:{port}/api/v1/health', headers={'X-Local-Token': token}).json()
            return result if not instance_id or result.get('instance_id') == instance_id else {'api':'foreign_instance','worker':'unknown'}
    except Exception:
        return {'api': 'unavailable', 'worker': 'unknown'}


def assert_data_directory_available(folder):
    for role in ['api', 'worker']:
        try:
            record = json.loads((folder / f'{role}.json').read_text('utf-8'))
            process = psutil.Process(record['pid'])
            entry = str(Path(record['root']) / 'scripts' / 'service_entry.py')
            if (abs(process.create_time() - record['started']) < 0.1 and entry in process.cmdline()
                and role in process.cmdline() and Path(record['root']).resolve() != ROOT.resolve()):
                raise SystemExit('Another live installation already uses this data directory. Stop it before changing installations.')
        except (OSError, ValueError, KeyError, psutil.Error):
            pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    settings = Settings()
    from app.security.configuration import configuration_lock
    with configuration_lock(settings.data_dir / 'runtime' / 'services'):
        manage(args, settings)


def manage(args, settings):
    folder, logs = settings.data_dir / 'runtime', settings.data_dir / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    assert_data_directory_available(folder)
    from app.domain.identifiers import new_id
    from app.local_runtime.manager import LocalModelManager
    from app.local_runtime.configuration import LocalRuntimeSettings
    from app.security.execution_policy import ExecutionSettings
    port, instance_id = 8765, new_id()
    if owned_process(folder, 'api'):
        record = json.loads((folder / 'api.json').read_text('utf-8'))
        port, instance_id = record.get('port', 8765), record.get('instance_id')
    if args.action == 'start':
        if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
            raise SystemExit('Run npm --prefix frontend run build first.')
        env_file = settings.configuration_root / '.env.local'
        if not env_file.exists():
            env_file.write_text('', 'utf-8')
        local = LocalRuntimeSettings(settings)
        if ExecutionSettings(settings.data_dir).view()['model_mode'] == 'local' and local.view()['manifest_path']:
            local.manager().start(local.view()['manifest_path'])
        if not owned_process(folder, 'api'):
            port = LocalModelManager.available_port(8765)
        for role in ['api', 'worker']:
            if owned_process(folder, role):
                continue
            with (logs / f'{role}.log').open('ab') as log:
                launched = subprocess.Popen([sys.executable, str(ROOT / 'scripts' / 'service_entry.py'), role],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    env={**os.environ, 'PYTHONUNBUFFERED': '1', 'SOC_API_PORT': str(port), 'SOC_INSTANCE_ID': instance_id or ''})
            deadline = time.monotonic() + 20
            while not owned_process(folder, role) and time.monotonic() < deadline:
                if launched.poll() is not None:
                    raise SystemExit(f'{role} exited. See data/logs/{role}.log')
                time.sleep(0.2)
            if not owned_process(folder, role):
                raise SystemExit(f'{role} did not register. See data/logs/{role}.log')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            health = api_health(port, instance_id)
            if health.get('api') == 'ready' and health.get('worker') == 'running':
                (folder / 'entry.json').write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'instance_id': instance_id}), 'utf-8')
                print(f'Laura ready: http://127.0.0.1:{port}')
                return
            time.sleep(0.5)
        raise SystemExit('Service health did not become ready. See data/logs.')
    if args.action == 'stop':
        for role in ['worker', 'api']:
            process = owned_process(folder, role)
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    raise SystemExit(f'{role} is still stopping; no forced kill was issued.')
        LocalRuntimeSettings(settings).manager().stop()
        print('Workspace services stopped. Interrupted work requires explicit resume after restart.')
        return
    print(json.dumps({'processes': {role: bool(owned_process(folder, role)) for role in ['api', 'worker']},
        'local_llm': LocalRuntimeSettings(settings).manager().status(), 'health': api_health(port, instance_id)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
