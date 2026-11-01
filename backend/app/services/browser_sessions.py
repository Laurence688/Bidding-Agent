"""Launcher-only page presence; no durable browser identity or business data."""
import json
import secrets
import threading
import time
from pathlib import Path


class BrowserLease:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.RLock()
        self.token, self.clients, self.seen, self.empty_since = None, set(), False, None

    def _select(self, token):
        if token != self.token:
            self.token, self.clients, self.seen, self.empty_since = token, set(), False, None

    def connect(self, token, client):
        with self.lock:
            self._select(token)
            self.clients.add(client)
            self.seen, self.empty_since = True, None

    def disconnect(self, token, client):
        with self.lock:
            if token != self.token:
                return
            self.clients.discard(client)
            if not self.clients and self.empty_since is None:
                self.empty_since = self.clock()

    def status(self, token):
        with self.lock:
            self._select(token)
            return {'clients': len(self.clients), 'seen': self.seen,
                    'idle_seconds': max(0, self.clock() - self.empty_since) if self.empty_since is not None else 0}


def current_launcher_token(data_dir):
    from app.worker.leases import owner_alive
    try:
        record = json.loads((Path(data_dir) / 'runtime/browser-launcher.json').read_text('utf-8'))
        token = record['token']
        if isinstance(token, str) and 32 <= len(token) <= 128 and owner_alive(record['pid'], record['started']):
            return token
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def install_browser_routes(app, settings):
    import asyncio
    from fastapi import Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
    from urllib.parse import urlsplit
    lease = BrowserLease()
    app.state.browser_lease = lease

    @app.get('/api/launcher/status')
    def status(request: Request):
        expected = current_launcher_token(settings.data_dir)
        supplied = request.headers.get('x-launcher-token', '')
        if not expected or not secrets.compare_digest(expected, supplied):
            return JSONResponse(status_code=403, content={'error': 'LAUNCHER_SESSION_DENIED'})
        result=lease.status(expected)
        updates=getattr(app.state,'updates',None)
        if updates and updates.launcher_update_token():result['update_token']=updates.launcher_update_token()
        return result

    @app.websocket('/api/launcher/session')
    async def page(websocket: WebSocket):
        if (websocket.url.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or websocket.headers.get('origin') not in settings.local['allowed_origins']
                or urlsplit(websocket.headers.get('origin', '')).netloc != websocket.headers.get('host')):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        token, client = None, secrets.token_urlsafe(16)
        try:
            body = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            expected = current_launcher_token(settings.data_dir)
            supplied = body.get('token') if isinstance(body, dict) else None
            if not expected or not isinstance(supplied, str) or not secrets.compare_digest(expected, supplied):
                await websocket.close(code=4403)
                return
            token = expected
            lease.connect(token, client)
            await websocket.send_json({'accepted': True})
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
            pass
        finally:
            if token:
                lease.disconnect(token, client)
