from app.telemetry.storage import records
from fastapi import Request
from pydantic import Field
from app.schemas.core import StrictModel
from app.domain.errors import DomainError
from app.telemetry.feedback import FeedbackService
from app.telemetry.sync import SyncWorker
from app.telemetry.tracing import destination


class Selection(StrictModel):
    clause_ids: list[str] = Field(min_length=1, max_length=1000)


class Publication(StrictModel):
    feedback_ids: list[str] = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=100)


class FeedbackResolution(StrictModel):
    expected_version: int
    gold: dict


def install_telemetry_routes(app, repo, settings, idem):
    service = FeedbackService(repo, settings)

    @app.get('/api/v1/langsmith/status')
    def status():
        return {'enabled': settings.langsmith.get('enabled', False), 'destination': destination(settings),
            'key_configured': bool(settings.secrets.get('LANGSMITH_API_KEY')),
            'annotation_queue_configured': bool(settings.langsmith.get('annotation_queue_id')),
            'paused': any(i.get('paused') and i.get('destination') == destination(settings) for i in repo.list('cloud_sync_control'))}

    @app.get('/api/v1/runs/{run_id}/langsmith')
    def detail(run_id: str):
        repo.get('run', run_id)
        roots = service.roots(run_id)
        result = []
        for trace in roots:
            meta = trace['payload']['extra']['metadata']
            binding = service.binding(trace)
            result.append({'id': trace['id'], 'trace_id': trace['payload']['id'], 'status': trace['status'],
                'clause_id': meta.get('clause_id'), 'result_id': meta.get('result_id'),
                'decision': trace['payload'].get('outputs', {}).get('decision'),
                'reason_codes': trace['payload'].get('outputs', {}).get('reason_codes', []),
                'raw_text': trace['payload']['inputs'].get('raw_text'), 'attempts': trace['attempts'],
                'stale': bool(binding and binding.get('result_id') != meta.get('result_id')),
                'error_code': trace.get('error_code'), 'synced_at': trace.get('synced_at'),
                'url': settings.langsmith.get('web_url', 'https://smith.langchain.com').rstrip('/') + '/runs/' + trace['payload']['id'] if trace['status'] == 'synced' else None})
        queue_records = [i for i in records(repo, 'cloud_outbox') if i.get('run_id') == run_id]
        return {**status(), 'items': result, 'counts': {s: sum(i['status'] == s for i in queue_records) for s in ('pending', 'sending', 'synced', 'failed')},
            'feedback': [i for i in records(repo, 'cloud_feedback') if i.get('run_id') == run_id]}

    @app.post('/api/v1/langsmith/outbox/{ident}/retry')
    def retry(ident: str):
        return {k: v for k, v in SyncWorker(repo, settings).retry(ident).items() if k != 'payload'}

    @app.post('/api/v1/runs/{run_id}/langsmith/retry')
    def retry_run(run_id: str):
        repo.get('run', run_id)
        for item in records(repo, 'cloud_outbox'):
            if item.get('run_id') == run_id and item['status'] == 'failed':
                SyncWorker(repo, settings).retry(item['id'])
        return detail(run_id)

    @app.post('/api/v1/langsmith/resume')
    def resume():
        SyncWorker(repo, settings).resume()
        return status()

    @app.post('/api/v1/runs/{run_id}/langsmith/annotations', status_code=202)
    def annotations(run_id: str, body: Selection, request: Request):
        return idem(request, '', 'annotation:' + run_id, body.model_dump(), lambda: service.annotate(run_id, body.clause_ids))

    @app.post('/api/v1/runs/{run_id}/langsmith/feedback-pull', status_code=202)
    def pull(run_id: str, request: Request):
        return idem(request, '', 'feedback-pull:' + run_id, {}, lambda: service.pull(run_id))

    @app.post('/api/v1/langsmith/feedback/preview')
    def preview(body: list[dict]):
        return service.preview(body)

    @app.put('/api/v1/langsmith/feedback/{ident}')
    def resolve(ident: str, body: FeedbackResolution):
        return service.resolve(ident, body.expected_version, body.gold)

    @app.post('/api/v1/langsmith/feedback/publish', status_code=201)
    def publish(body: Publication):
        return service.publish(body.feedback_ids, body.name)
