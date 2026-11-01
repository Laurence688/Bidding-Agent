"""Manage only the exact llama-server process started for this user data directory."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from app.domain.errors import DomainError
from app.domain.identifiers import new_id, utc_now
from app.local_runtime.artifacts import validate_artifact
from app.security.configuration import configuration_lock, atomic_write


class LocalModelManager:
    def __init__(self, data_dir, model_root):
        self.data_dir, self.model_root = Path(data_dir).resolve(), Path(model_root).resolve()
        self.state_path = self.data_dir / 'runtime' / 'local-model.json'
        self.api_key_path = self.data_dir / 'runtime' / 'local-model.key'

    def _state(self):
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text('utf-8'))
        except (ValueError, OSError):
            raise DomainError('LOCAL_MODEL_STATE_INVALID', '本地模型进程记录损坏，无法确认归属', 409)

    def _owned(self, state):
        try:
            process = psutil.Process(state['pid'])
            return (abs(process.create_time() - state['created_at']) < 0.01
                    and process.cmdline() == state['command'])
        except (KeyError, psutil.Error):
            return False

    def status(self):
        state = self._state()
        if not state:
            return {'state': 'stopped', 'business_accepted': False, 'generation_verified': False}
        if not self._owned(state):
            return {**state, 'state': 'stopped' if not psutil.pid_exists(state.get('pid', -1)) else 'foreign_process', 'business_accepted': False, 'generation_verified': False}
        rss = psutil.Process(state['pid']).memory_info().rss
        return {**state, 'rss_bytes': rss, 'business_accepted': False}

    def record_generation(self, instance_id, artifact_identity, schema_hash):
        """Record protocol/schema readiness, without asserting business correctness."""
        with configuration_lock(self.data_dir / 'runtime'):
            state = self._state()
            if (not self._owned(state) or state.get('instance_id') != instance_id
                or state.get('artifact_identity') != artifact_identity):
                return False
            state.update(generation_verified=True, generation_verified_at=utc_now(), generation_schema_hash=schema_hash)
            atomic_write(self.state_path, json.dumps(state, ensure_ascii=False))
            return True

    @staticmethod
    def available_port(start=8766):
        for port in range(start, start + 100):
            with socket.socket() as sock:
                try:
                    if os.name == 'nt':
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    sock.bind(('127.0.0.1', port))
                    return port
                except OSError:
                    continue
        raise DomainError('LOCAL_MODEL_PORT_BUSY', '本机模型端口均被占用', 409)

    def start(self, manifest_path, ready_timeout=120, start_port=8766):
        path = Path(manifest_path)
        if not path.is_file():
            raise DomainError('LOCAL_MODEL_MANIFEST_MISSING', '未配置通过校验的模型清单，无法启动', 409)
        try:
            manifest = json.loads(path.read_text('utf-8'))
        except ValueError:
            raise DomainError('ARTIFACT_MANIFEST_INVALID', '模型清单格式无效', 422)
        from app.rag.resource_pool import heavy_lease
        with heavy_lease(self.data_dir, 'model_load'), configuration_lock(self.data_dir / 'runtime'):
            current = self._state()
            from app.domain.identifiers import content_hash
            if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),list) or any(not isinstance(f,dict) or not isinstance(f.get('path'),str) for f in manifest['files']):
                raise DomainError('ARTIFACT_MANIFEST_INVALID','模型清单缺少合法文件列表',422)
            signatures = {f['path']: (p.stat().st_size, p.stat().st_mtime_ns) if (p := self.model_root / f['path']).is_file() else None
                          for f in manifest.get('files', [])}
            verified = None
            if self._owned(current):
                if (current.get('artifact_identity') == content_hash(manifest)
                    and current.get('file_signatures') == json.loads(json.dumps(signatures))):
                    return {**self.status(), 'reused': True}
                verified = validate_artifact(manifest, self.model_root)
                self._stop(current)
            elif current and psutil.pid_exists(current.get('pid', -1)):
                raise DomainError('LOCAL_MODEL_NOT_OWNED', '已有进程记录无法确认归属，未停止或替换', 409)
            verified = verified or validate_artifact(manifest, self.model_root)
            port = self.available_port(start_port)
            command = [str(verified['runtime_path']), '--model', str(verified['model_path']), '--host', '127.0.0.1',
                       '--port', str(port), '--ctx-size', '8192', '--threads', '4', '--parallel', '1', '--n-gpu-layers', '0',
                       '--alias', manifest['model_id'], '--no-webui', '--api-key-file', self.api_key_path.name]
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            import secrets
            atomic_write(self.api_key_path, secrets.token_urlsafe(32))
            log_path = self.state_path.parent / 'local-model.log'
            with log_path.open('ab') as log:
                process = subprocess.Popen(command, cwd=self.state_path.parent, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                created_at=psutil.Process(process.pid).create_time()
            except psutil.NoSuchProcess:
                self.api_key_path.unlink(missing_ok=True)
                raise DomainError('LOCAL_MODEL_START_FAILED','模型进程在登记前退出，请查看本地模型日志',503)
            state = {'pid': process.pid, 'created_at': created_at, 'command': command,
                     'instance_id': new_id(), 'port': port, 'artifact_identity': verified['identity'],
                     'file_signatures': signatures, 'last_activity': time.time(),
                     'model_id': manifest['model_id'], 'state': 'loading', 'log_path': str(log_path)}
            atomic_write(self.state_path, json.dumps(state, ensure_ascii=False))
            started = time.monotonic()
            try:
                with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
                    while time.monotonic() - started < ready_timeout:
                        if process.poll() is not None:
                            raise DomainError('LOCAL_MODEL_START_FAILED', '模型进程已退出，请查看本地模型日志', 503)
                        try:
                            response = client.get(f'http://127.0.0.1:{port}/health')
                            owns_port = any(c.status == psutil.CONN_LISTEN and c.laddr.port == port for c in psutil.Process(process.pid).net_connections(kind='tcp'))
                            if response.status_code == 200 and self._owned(state) and owns_port:
                                state.update(state='ready', cold_start_seconds=time.monotonic() - started)
                                atomic_write(self.state_path, json.dumps(state, ensure_ascii=False))
                                return self.status()
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.2)
                raise DomainError('LOCAL_MODEL_START_TIMEOUT', '模型冷加载超过等待上限', 503)
            except Exception:
                if self._owned(state):
                    self._stop(state)
                elif self._state().get('instance_id')==state['instance_id'] and process.poll() is not None:
                    self.state_path.unlink(missing_ok=True)
                    self.api_key_path.unlink(missing_ok=True)
                raise

    def _stop(self, state):
        if not self._owned(state):
            raise DomainError('LOCAL_MODEL_NOT_OWNED', '无法确认进程归属，未停止其他本机服务', 409)
        process = psutil.Process(state['pid'])
        process.terminate()
        try:
            process.wait(timeout=10)
        except psutil.TimeoutExpired:
            if self._owned(state):
                process.kill()
                process.wait(timeout=5)
        self.state_path.unlink(missing_ok=True)
        self.api_key_path.unlink(missing_ok=True)

    def touch(self):
        with configuration_lock(self.data_dir / 'runtime'):
            state = self._state()
            if self._owned(state):
                state['last_activity'] = time.time()
                atomic_write(self.state_path, json.dumps(state, ensure_ascii=False))

    def release_idle(self, idle_seconds=600):
        from app.rag.resource_pool import heavy_lease
        try:
            with heavy_lease(self.data_dir, 'idle_release', timeout_seconds=0), configuration_lock(self.data_dir / 'runtime'):
                state = self._state()
                if self._owned(state) and time.time() - state.get('last_activity', time.time()) >= idle_seconds:
                    self._stop(state)
        except DomainError as exc:
            if exc.code != 'RESOURCE_BUSY':
                raise

    def stop(self):
        from app.rag.resource_pool import heavy_lease
        with heavy_lease(self.data_dir, 'model_stop'), configuration_lock(self.data_dir / 'runtime'):
            state = self._state()
            if not state:
                return self.status()
            if not psutil.pid_exists(state.get('pid', -1)):
                self.state_path.unlink(missing_ok=True)
                self.api_key_path.unlink(missing_ok=True)
            else:
                self._stop(state)
            return self.status()
