"""Run separately: python -m app.telemetry.sync [--once]."""
from app.telemetry.storage import records
import argparse
import time
from datetime import datetime
from app.domain.errors import DomainError
from app.domain.identifiers import utc_now
from app.telemetry.tracing import destination


def http_status(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        status = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
        if status:
            return status
        exc = exc.__cause__ or exc.__context__


class SyncWorker:
    def __init__(self, repo, settings, client_factory=None, now=time.time):
        self.repo, self.settings, self.now = repo, settings, now
        self.client_factory = client_factory or self._client

    def _client(self):
        if not self.settings.secrets.get('LANGSMITH_API_KEY'):
            raise DomainError('LANGSMITH_KEY_REQUIRED', '请在 .env.local 配置 LangSmith 密钥', 409)
        from app.security.execution_policy import policy_for, assert_endpoint_allowed
        endpoint = self.settings.langsmith['endpoint']
        assert_endpoint_allowed(policy_for(self.settings, extra_services={'sync': [endpoint]}), endpoint, 'sync')
        from langsmith import Client
        from urllib3.util.retry import Retry
        import requests
        settings = self.settings
        class GuardedSession(requests.Session):
            def send(self, request, **kwargs):
                assert_endpoint_allowed(policy_for(settings, extra_services={'sync': [endpoint]}), request.url, 'sync')
                return super().send(request, **kwargs)
        session = GuardedSession()
        session.trust_env = False
        cfg = self.settings.langsmith
        return Client(api_url=cfg['endpoint'], api_key=self.settings.secrets.get('LANGSMITH_API_KEY'),
            workspace_id=cfg.get('workspace_id') or None, auto_batch_tracing=False,
            omit_traced_runtime_info=True, tracing_sampling_rate=1.0, timeout_ms=10000, retry_config=Retry(total=0), session=session)

    def retry(self, ident):
        with self.repo.transaction():
            item = self.repo.get('cloud_outbox', ident)
            if item['status'] == 'synced':
                return item
            if item['status'] == 'sending' and item.get('lease_until', 0) > self.now():
                raise DomainError('SYNC_IN_FLIGHT', '记录正在发送，请等待当前请求结束', 409)
            return self.repo.update('cloud_outbox', ident, {'status': 'pending', 'attempts': 0, 'next_attempt': 0, 'error_code': None})

    def resume(self):
        with self.repo.transaction():
            for control in self.repo.list('cloud_sync_control'):
                self.repo.update('cloud_sync_control', control['id'], {'paused': False})

    def step(self):
        if not getattr(self.settings, 'langsmith', {}).get('enabled'):
            return False
        dest = destination(self.settings)
        controls = self.repo.list('cloud_sync_control')
        if any(c.get('paused') and c.get('destination') == dest for c in controls):
            return False
        # One short claim transaction; leases expire after a crashed sender.
        with self.repo.transaction():
            items = records(self.repo, 'cloud_outbox')
            done = {i['id'] for i in items if i['status'] == 'synced'}
            sent_runs = {i['payload'].get('id') for i in items if i['operation'] == 'trace' and i['status'] == 'synced'}
            candidates = [i for i in items if i['destination'] == dest
                and (i['status'] == 'pending' or (i['status'] == 'sending' and i.get('lease_until', 0) < self.now()))
                and i.get('next_attempt', 0) <= self.now() and set(i.get('dependencies', [])) <= done
                and (not i['payload'].get('parent_run_id') or i['payload']['parent_run_id'] in sent_runs)]
            if not candidates:
                return False
            item = self.repo.update('cloud_outbox', candidates[0]['id'], {'status': 'sending', 'lease_until': self.now() + 60})
        try:
            client = self.client_factory()
            try:
                result = self.deliver(client, item)
            finally:
                close = getattr(client, 'close', None)
                if close:
                    close()
            if item['operation'] == 'feedback_pull':
                from app.telemetry.feedback import FeedbackService
                result = FeedbackService(self.repo, self.settings).preview(result['feedback'])
            self.repo.update('cloud_outbox', item['id'], {'status': 'synced', 'synced_at': utc_now(), 'cloud': result})
        except Exception as exc:
            if getattr(exc, 'code', None) == 'EXECUTION_OFFLINE':
                self.repo.update('cloud_outbox', item['id'], {'status':'pending','error_code':'OFFLINE_PAUSED',
                    'lease_until':0,'next_attempt':self.now()+1})
                return False
            status = http_status(exc)
            kind = type(exc).__name__
            auth = status in {401, 403} or kind in {'LangSmithAuthError', 'LangSmithPermissionError'} or getattr(exc, 'code', None) == 'LANGSMITH_KEY_REQUIRED'
            attempts = item['attempts'] + 1
            transient = status in {408, 429, 500, 502, 503, 504} or isinstance(exc, (TimeoutError, ConnectionError)) or kind in {'LangSmithConnectionError', 'LangSmithRateLimitError', 'LangSmithAPIError', 'LangSmithRequestTimeout', 'ReadTimeout', 'ConnectTimeout'}
            self.repo.update('cloud_outbox', item['id'], {'status': 'pending' if transient and attempts < 5 and not auth else 'failed',
                'attempts': attempts, 'error_code': 'AUTH_PAUSED' if auth else kind, 'next_attempt': self.now() + min(2**attempts, 300)})
            if auth:
                with self.repo.transaction():
                    self.repo.put('cloud_sync_control', {'paused': True, 'destination': dest})
        return True

    def deliver(self, client, item):
        payload = dict(item['payload'])
        if item['operation'] == 'trace':
            for field in ('start_time', 'end_time'):
                payload[field] = datetime.fromisoformat(payload[field].replace('Z', '+00:00'))
            try:
                client.create_run(**{**payload, 'project_name': payload.get('project_name', item['destination']['project'])})
            except Exception as exc:
                if type(exc).__name__ != 'LangSmithConflictError' and http_status(exc) != 409:
                    raise
            # SDK 0.14.12 silently ignores 409, so always confirm persistence.
            try:
                remote = client.read_run(payload['id'])
            except Exception as exc:
                if type(exc).__name__ == 'LangSmithNotFoundError' or http_status(exc) == 404:
                    raise TimeoutError('CLOUD_NOT_VISIBLE_YET') from exc
                raise
            if str(remote.id) != payload['id'] or remote.inputs != payload['inputs'] or remote.outputs != payload['outputs']:
                raise ValueError('CLOUD_ID_CONFLICT')
            return {'id': payload['id']}
        from app.telemetry.experiments import deliver_operation
        return deliver_operation(client, item)


def main():
    from app.config import Settings
    from app.persistence.repositories import Repository
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        settings = Settings()
        worked = SyncWorker(Repository(settings.data_dir), settings).step()
        if args.once:
            break
        if not worked:
            time.sleep(1)


if __name__ == '__main__':
    main()
