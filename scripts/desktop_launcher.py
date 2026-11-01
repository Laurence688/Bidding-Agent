"""Icon launcher: ordinary browser tabs own this installation's service lifetime."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time


def run_session(backend, browser, emit=print):
    try:
        emit('LAURA_STATUS:正在启动本地服务…')
        url = backend.start()
        emit('LAURA_STATUS:正在打开浏览器页面…')
        browser.open(url)
        emit('LAURA_READY')
        browser.wait_until_closed()
    finally:
        try:
            browser.close()
        finally:
            backend.stop()


class Backend:
    def __init__(self, root):
        self.root, self.settings, self.started = Path(root), None, False
        self.token = secrets.token_urlsafe(32)
        self.preexisting, self.engaged = False, False

    def command(self, action, timeout):
        from app.config import Settings
        self.settings = self.settings or Settings(root=self.root)
        logs = self.settings.data_dir / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 'desktop-launcher.log').open('ab') as log:
            result = subprocess.run(
                [sys.executable, str(self.root / 'scripts/service_manager.py'), action],
                cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
        if result.returncode:
            raise RuntimeError('本地服务操作失败，请查看 data/logs/desktop-launcher.log。')

    def start(self):
        from app.config import Settings
        from app.security.configuration import atomic_write
        import psutil
        self.settings = Settings(root=self.root)
        sys.path.insert(0, str(self.root / 'scripts'))
        from service_manager import owned_process, assert_data_directory_available
        folder = self.settings.data_dir / 'runtime'
        assert_data_directory_available(folder)
        self.preexisting = any(owned_process(folder, role) for role in ('api', 'worker'))
        self.record = self.settings.data_dir / 'runtime/browser-launcher.json'
        atomic_write(self.record, json.dumps({'token': self.token, 'pid': os.getpid(), 'started': psutil.Process().create_time()}))
        self.started = True
        self.command('start', 95)
        entry = json.loads((self.settings.data_dir / 'runtime/entry.json').read_text('utf-8'))
        from urllib.parse import urlsplit
        parsed = urlsplit(entry['url'])
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
            raise RuntimeError('本地服务入口无效。')
        self.url = entry['url']
        self.status()
        self.engaged = True
        return self.url

    def status(self):
        import httpx
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get(self.url + '/api/launcher/status', headers={'X-Launcher-Token': self.token})
            if response.status_code != 200:
                raise RuntimeError('本地服务版本需要更新，请先运行 Stop.ps1，再双击 Laura.exe。')
            return response.json()

    def stop(self):
        if self.started:
            try:
                if not self.preexisting or self.engaged:
                    self.command('stop', 40)
            finally:
                self.record.unlink(missing_ok=True)


def page_session_finished(status, initial_elapsed):
    if not status['seen']:
        if initial_elapsed > 90:
            raise RuntimeError('浏览器页面未能连接。请检查默认浏览器后重新打开 Laura。')
        return False
    return status['clients'] == 0 and status['idle_seconds'] >= 8


class Browser:
    def __init__(self, backend, focus, cancelled, emit, update_ready=None, update_failed=None):
        self.backend, self.focus, self.cancelled, self.emit = backend, focus, cancelled, emit
        self.update_ready, self.update_failed = update_ready, update_failed

    def open(self, url):
        self.url = url
        if self.cancelled.is_set():
            return
        # The native host opens the default browser OUTSIDE the backend Job.
        self.emit('LAURA_OPEN:' + url)

    def wait_until_closed(self):
        started, failures = time.monotonic(), 0
        while not self.cancelled.wait(.5):
            if self.focus.is_set():
                self.focus.clear()
                self.emit('LAURA_OPEN:' + self.url)
            try:
                status = self.backend.status()
                failures = 0
            except Exception:
                failures += 1
                if failures >= 6:
                    raise RuntimeError('本地服务连接已中断，请重新启动 Laura。')
                continue
            update_token=status.get('update_token')
            if update_token:
                import re
                if not isinstance(update_token,str) or not re.fullmatch('[a-f0-9]{32}',update_token):
                    raise RuntimeError('更新请求格式无效，已阻止安装。')
                self.emit('LAURA_UPDATE:'+update_token)
                if self.update_ready is None:
                    return
                deadline=time.monotonic()+100
                while not self.cancelled.wait(.2):
                    if self.update_ready.is_set():return
                    if self.update_failed.is_set():
                        self.update_failed.clear()
                        break
                    if time.monotonic()>deadline:
                        raise RuntimeError('更新组件未能确认启动，请重新打开Laura；原资料保留。')
                continue
            if page_session_finished(status, time.monotonic() - started):
                return

    def close(self):
        # Never close or terminate any browser process.
        pass


def main():
    if os.name != 'nt':
        raise RuntimeError('Laura 图标启动器仅支持 Windows。')
    print('LAURA_PID:' + str(os.getpid()), flush=True)
    if sys.stdin.readline().strip() != 'START':
        return
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / 'backend'))
    focus, cancelled = threading.Event(), threading.Event()
    update_ready, update_failed = threading.Event(), threading.Event()
    def commands():
        for line in sys.stdin:
            if line.strip() == 'FOCUS':
                focus.set()
            elif line.strip() == 'CLOSE':
                cancelled.set()
            elif line.strip() == 'UPDATE_READY':
                update_ready.set()
            elif line.strip() == 'UPDATE_FAILED':
                update_failed.set()
        cancelled.set()
    threading.Thread(target=commands, daemon=True).start()
    backend = Backend(root)
    emit = lambda s: print(s, flush=True)
    run_session(backend, Browser(backend, focus, cancelled, emit, update_ready, update_failed), emit)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('LAURA_ERROR:' + str(exc), flush=True)
        sys.exit(1)
