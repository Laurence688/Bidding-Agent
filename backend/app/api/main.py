import asyncio
import json
import secrets
import os
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from app.config import Settings
from app.domain.errors import DomainError
from app.domain.identifiers import new_id
from app.persistence.repositories import Repository
from app.security.local_access import LocalAccess
from app.services.tasks import TaskService
from app.services.prompts import PromptService
from app.services.files import FileService
from app.services.excel import ExcelService
from app.services.sessions import SessionService
from app.services.reviews import ReviewService
from app.services.run_reports import RunReportService
from app.services.review_feedback import ReviewFeedbackService
from app.services.scope_plans import ScopePlanService
from app.services.review_workbench import ReviewWorkbench
from app.services.libraries import LibraryService
from app.services.result_sources import result_sources
from app.excel.review_report import review_workbook
from app.services.observability import ObservabilityService
from app.services.decision_observability import DecisionObservabilityService
from app.services.exports import ExportService
from app.model_gateway.usage import summarize_usage
from app.worker.leases import reconcile_interrupted, owner_alive
from app.worker.events import read_events
from app.api.contracts import *
from app.schemas.excel import MappingSelection


def create_app(settings=None):
    settings = settings or Settings()
    repo = Repository(settings.data_dir)
    token = secrets.token_urlsafe(32)
    access = LocalAccess(token, settings.local['allowed_origins'])
    reconcile_interrupted(repo)
    app = FastAPI(title='SOC 投标助手', docs_url='/api/docs', redoc_url=None)
    app.state.repo, app.state.settings = repo, settings
    from app.services.browser_sessions import install_browser_routes
    install_browser_routes(app, settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.local['allowed_origins'], allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                       allow_headers=['Content-Type', 'X-Local-Token', 'Idempotency-Key', 'Last-Event-ID'])

    def fail(exc, request):
        return JSONResponse(status_code=exc.status, content={'error': {'code': exc.code, 'message': exc.message,
            'request_id': getattr(request.state, 'request_id', new_id()), 'retryable': exc.retryable, 'details': exc.details}})

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return fail(exc, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return fail(DomainError('VALIDATION_ERROR', '字段缺失或格式不正确', 422,
            {'fields': [{'location': list(e['loc']), 'type': e['type']} for e in exc.errors()]}), request)

    @app.middleware('http')
    async def local_only(request, call_next):
        request.state.request_id = new_id()
        try:
            if request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
                raise DomainError('HOST_DENIED', '仅允许本机访问', 403)
            origin = request.headers.get('origin')
            if origin and origin not in access.origins:
                raise DomainError('ORIGIN_DENIED', '请求来源不允许', 403)
            if request.url.path.startswith('/api/v1/') and request.url.path != '/api/v1/bootstrap' and request.method != 'OPTIONS':
                access.check(origin, request.headers.get('x-local-token'))
            response = await call_next(request)
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
            return response
        except DomainError as exc:
            return fail(exc, request)

    def paginate(items, limit):
        return {'items': items[:limit], 'next_cursor': items[limit - 1]['id'] if len(items) > limit else None}

    def records(kind, project_id=None, parent_id=None, cursor=None, limit=50):
        return paginate(repo.list(kind, project_id, parent_id, cursor, min(limit, 200) + 1), min(limit, 200))

    def idem(request, project_id, operation, body, action):
        return repo.idempotent(project_id, operation, request.headers.get('Idempotency-Key'), body, lambda db: action())

    def usage(run_ids):
        return summarize_usage([a for run_id in run_ids for a in RunReportService(repo, settings).attempts(run_id)])

    @app.get('/api/v1/docs')
    def document_list(scope: str = 'current', q: str = Query(default='', max_length=200)):
        from app.services.documents import DocumentService
        return DocumentService(settings.root,include_archive=False).list(scope, q)

    @app.get('/api/v1/docs/tree')
    def document_tree():
        from app.services.documents import DocumentService
        return DocumentService(settings.root,include_archive=False).tree()

    @app.get('/api/v1/docs/assets/{asset_id}')
    def document_asset(asset_id: str):
        from app.services.documents import DocumentService
        path=DocumentService(settings.root).asset(asset_id)
        return FileResponse(path,media_type='image/png' if path.suffix.lower()=='.png' else 'image/webp')

    @app.get('/api/v1/docs/{document_id}')
    def document_detail(document_id: str):
        from app.services.documents import DocumentService
        return DocumentService(settings.root,include_archive=False).get(document_id)

    @app.get('/api/v1/bootstrap')
    def bootstrap():
        from app.services.libraries import LibraryService
        from app.services.browser_sessions import current_launcher_token
        return {'token': token, 'local_user_id': repo.local_user_id, 'products': LibraryService(repo,settings).products(),
                'launcher_session': current_launcher_token(settings.data_dir)}

    @app.get('/api/v1/health')
    def health():
        reconcile_interrupted(repo)
        with repo.connection() as db:
            lease = db.execute('SELECT * FROM worker_leases WHERE name=?', ('main',)).fetchone()
        return {'api': 'ready', 'worker': 'running' if lease and owner_alive(lease['pid'], lease['started']) else 'stopped',
                'instance_id': getattr(settings, 'runtime_instance_id', None)}

    @app.get('/api/v1/products')
    def products():
        from app.services.libraries import LibraryService
        return LibraryService(repo,settings).products()

    @app.get('/api/v1/projects')
    def projects(cursor: str | None = None, limit: int = Query(50, ge=1, le=200), scope: str = 'active'):
        if scope not in {'active', 'trash'}:
            raise DomainError('PROJECT_SCOPE_INVALID', '请选择正常项目或回收站', 422)
        items = repo.list('project', after=cursor, limit=limit+1, trashed=scope=='trash')
        return {'items': items[:limit], 'next_cursor': items[limit-1]['id'] if len(items)>limit else None}

    @app.post('/api/v1/projects/{project_id}/trash')
    def trash_project(project_id: str, body: VersionMutation):
        return repo.set_project_trashed(project_id, body.expected_version, True)

    @app.post('/api/v1/projects/{project_id}/restore')
    def restore_project(project_id: str, body: VersionMutation):
        return repo.set_project_trashed(project_id, body.expected_version, False)

    @app.post('/api/v1/projects', status_code=201)
    def create_project(body: ProjectCreate):
        try:
            from app.services.libraries import LibraryService
            product = LibraryService(repo,settings).resolve_product(body.default_product_id)
        except ValueError as exc:
            raise DomainError('PRODUCT_REQUIRED', '请选择有效产品', 422) from exc
        return repo.create_project(body.name, product)

    @app.get('/api/v1/projects/{project_id}')
    def get_project(project_id: str):
        return repo.get('project', project_id)

    @app.patch('/api/v1/projects/{project_id}')
    def patch_project(project_id: str, body: ProjectPatch):
        changes = body.model_dump(exclude_none=True, exclude={'expected_version'})
        if 'default_product_id' in changes:
            from app.services.libraries import LibraryService
            changes['default_product_id'] = LibraryService(repo,settings).resolve_product(changes['default_product_id'])
        return repo.update_project(project_id, body.expected_version, changes)

    @app.get('/api/v1/projects/{project_id}/sessions')
    def sessions(project_id: str, cursor: str | None = None):
        repo.get('project', project_id)
        return records('session', project_id, cursor=cursor)

    @app.post('/api/v1/projects/{project_id}/sessions', status_code=201)
    def create_session(project_id: str, body: SessionCreate):
        return repo.create_session(project_id, body.name)

    @app.get('/api/v1/projects/{project_id}/observability')
    def observability(project_id: str, session_id: str | None = None):
        reconcile_interrupted(repo)
        return ObservabilityService(repo, settings).overview(project_id, session_id)

    @app.get('/api/v1/projects/{project_id}/observability/clauses')
    def observability_clauses(project_id: str, session_id: str | None = None, task_id: str | None = None,
                              decision: str | None = None, reason: str | None = None, cursor: str | None = None,
                              limit: int = Query(20, ge=1, le=100)):
        return DecisionObservabilityService(repo).clauses(project_id, session_id, task_id, decision, reason, cursor, limit)

    @app.get('/api/v1/runs/{run_id}/logs')
    def run_logs(run_id: str, cursor: str | None = None, limit: int = Query(100, ge=1, le=200)):
        return ObservabilityService(repo, settings).logs(run_id, cursor, limit)

    @app.get('/api/v1/sessions/{session_id}')
    def session_detail(session_id: str):
        session = repo.get_session(session_id)
        tasks = [t for t in repo.list('task', session['project_id']) if t.get('session_id') == session_id]
        ids = {r['id'] for t in tasks for r in repo.list('run', parent_id=t['id'])}
        return {**session, 'tasks': tasks, 'usage': usage(ids)}

    @app.get('/api/v1/sessions/{session_id}/messages')
    def messages(session_id: str, cursor: str | None = None, before: str | None = None, limit: int = Query(50,ge=1,le=100), run_id: str | None = None):
        from app.services.conversations import ConversationService
        return ConversationService(repo,settings).messages(session_id,before,limit,cursor,run_id)

    @app.patch('/api/v1/sessions/{session_id}')
    def rename_session(session_id: str, body: SessionPatch):
        return SessionService(repo,settings).update(session_id,body.expected_version,body.model_dump(exclude={'expected_version'},exclude_none=True))

    @app.post('/api/v1/sessions/{session_id}/copies', status_code=201)
    def copy_session(session_id: str, body: SessionCopy, request: Request):
        return SessionService(repo,settings).copy(session_id,body.expected_version,body.target_project_id,request.headers.get('Idempotency-Key'))

    @app.delete('/api/v1/sessions/{session_id}')
    def delete_session(session_id: str, body: SessionDelete):
        return SessionService(repo, settings).delete(session_id, body.expected_version, body.cancel_running)

    @app.post('/api/v1/projects/{project_id}/files', status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...), purpose: str = Form(...)):
        content = await file.read(50 * 1024 * 1024 + 1)
        record = FileService(repo).upload(project_id, file.filename or '', content, purpose)
        return {k: v for k, v in record.items() if k != 'managed_path'}

    @app.post('/api/v1/projects/{project_id}/excel/prechecks', status_code=202)
    def precheck(project_id: str, body: FileReference, request: Request):
        return idem(request, project_id, 'precheck', body.model_dump(), lambda: ExcelService(repo, settings).enqueue_precheck(project_id, body.file_id, body.session_id, body.product_id, body.task_note))

    @app.get('/api/v1/excel/prechecks/{precheck_id}')
    def get_precheck(precheck_id: str):
        from app.services.conversations import ConversationService
        return ConversationService(repo,settings).card(precheck_id)

    @app.post('/api/v1/excel/prechecks/{precheck_id}/mappings', status_code=201)
    def mapping(precheck_id: str, body: MappingSelection, request: Request, expected_version: int | None = Query(None,ge=1)):
        record = repo.get('precheck', precheck_id)
        return idem(request, record['project_id'], f'mapping:{precheck_id}', {'selection':body.model_dump(),'expected_version':expected_version}, lambda: ExcelService(repo, settings).mapping(precheck_id, body.model_dump(), expected_version))

    @app.put('/api/v1/excel/prechecks/{precheck_id}/mapping-selection')
    def card_selection(precheck_id: str, body: CardSelection, request: Request):
        from app.services.conversations import ConversationService
        card=repo.get('precheck',precheck_id)
        from pydantic import ValidationError
        try:selection=MappingSelection.model_validate(body.selection).model_dump() if body.selection is not None else None
        except ValidationError as exc:raise DomainError('VALIDATION_ERROR','映射配置格式无效',422) from exc
        return idem(request,card['project_id'],f'card-selection:{precheck_id}',body.model_dump(),lambda:ConversationService(repo,settings).selection(precheck_id,body.expected_version,selection))

    @app.post('/api/v1/excel/prechecks/{precheck_id}/confirm', status_code=202)
    def confirm_card(precheck_id: str, body: CardConfirm, request: Request):
        from app.services.conversations import ConversationService
        card=repo.get('precheck',precheck_id)
        return idem(request,card['project_id'],f'confirm-card:{precheck_id}',body.model_dump(),lambda:ConversationService(repo,settings).confirm(precheck_id,body.expected_version,body.mapping_id))

    @app.get('/api/v1/excel/mappings/{mapping_id}')
    def get_mapping(mapping_id: str):
        return repo.get('mapping', mapping_id)

    @app.post('/api/v1/projects/{project_id}/tasks', status_code=202)
    def create_task(project_id: str, body: TaskCreate, request: Request):
        return TaskService(repo, settings).enqueue(project_id, body.model_dump(exclude_none=True), request.headers.get('Idempotency-Key'))

    @app.get('/api/v1/projects/{project_id}/tasks')
    def project_tasks(project_id: str, cursor: str | None = None):
        repo.get('project', project_id)
        from app.services.task_labels import task_labels
        result=records('task', project_id, cursor=cursor)
        labels=task_labels(repo,project_id)
        result['items']=[{**item,**labels(item)} for item in result['items']]
        return result

    @app.get('/api/v1/tasks/{task_id}')
    def task_detail(task_id: str, run_id: str | None = None):
        reconcile_interrupted(repo)
        task = repo.get('task', task_id)
        run = repo.get('run', run_id or task['active_run_id'], task['project_id'])
        if run['task_id']!=task_id:raise DomainError('NOT_FOUND','运行不属于此任务',404)
        jobs = [j for j in repo.list('job', parent_id=run['id']) if j['kind'] == 'task']
        return {'task': task, 'run': run, 'scope_sources': ReviewWorkbench(repo,settings).scope_sources(run['id']), 'job': jobs[-1] if jobs else None, 'usage': usage({run['id']}), 'export_readiness': ExportService(repo, settings).get_export_readiness(run['id']).model_dump() if task['mode'] in {'SOC_SINGLE','SOC_BATCH','PRODUCT_QA'} else {'ready':False,'blockers':['DOCUMENT_TASK_USES_SEPARATE_ARTIFACTS']}}

    @app.get('/api/v1/tasks/{task_id}/runs')
    def runs(task_id: str, cursor: str | None = None):
        task = repo.get('task', task_id)
        return records('run', task['project_id'], task_id, cursor)

    @app.get('/api/v1/runs/{run_id}')
    def run_detail(run_id: str):
        run = repo.get('run', run_id)
        from app.services.translation_category import display_category
        snapshot=repo.get('config_snapshot', run['config_snapshot_id'])
        return {**run, 'config_snapshot': snapshot, 'display_category':display_category(run.get('mode'),snapshot), 'usage': usage({run_id})}

    @app.post('/api/v1/tasks/{task_id}/runs', status_code=202)
    def rerun(task_id: str, body: NewRun, request: Request):
        task = repo.get('task', task_id)
        return idem(request, task['project_id'], f'rerun:{task_id}', body.model_dump(), lambda: TaskService(repo, settings).new_run(task_id, body.expected_version, body.base_run_id, body.changes, body.product_id))

    @app.get('/api/v1/runs/{run_id}/clauses')
    def clauses(run_id: str, decision: str | None = None, review_status: str | None = None, cursor: str | None = None, limit: int = Query(50, ge=1, le=200)):
        run = repo.get('run', run_id)
        rows = []
        annotations = {}
        for item in repo.list('clause_annotation', run['project_id'], limit=100000):
            if item['run_id'] == run_id and item['revision'] > annotations.get(item['parent_id'], {}).get('revision', 0):
                annotations[item['parent_id']] = item
        for binding in repo.list('run_binding', run['project_id'], run_id, cursor, 100000):
            result = repo.get('clause_result', binding['result_id'], run['project_id'])['result'] if binding.get('result_id') else None
            if decision and (not result or result['decision'] != decision):
                continue
            if review_status and binding['review_status'] != review_status:
                continue
            annotation = annotations.get(binding['id'])
            rows.append({**binding, 'result': result, 'sources': result_sources(result), 'comments': repo.list('review_comment', run['project_id'], binding['id']),
                         'annotation': annotation, 'annotation_stale': bool(annotation and annotation['result_id'] != binding.get('result_id'))})
            if len(rows) > limit:
                break
        return paginate(rows, limit)

    @app.get('/api/v1/projects/{project_id}/run-reports')
    def run_reports(project_id: str):
        return RunReportService(repo, settings).versions(project_id)

    @app.get('/api/v1/runs/{run_id}/scope-plans')
    def scope_plans(run_id: str):
        run = repo.get('run', run_id)
        snapshot = repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
        return {'items': repo.list('scope_plan', run['project_id'], run_id, limit=1000), 'lifecycle_context': snapshot.get('lifecycle_context', {}), 'scope_sources': ReviewWorkbench(repo,settings).scope_sources(run_id)}

    @app.post('/api/v1/runs/{run_id}/scope-plans', status_code=201)
    def prepare_scope_plan(run_id: str, body: ScopePlanBody, request: Request):
        run = repo.get('run', run_id)
        return idem(request, run['project_id'], f'scope-plan:{run_id}', body.model_dump(mode='json'),
                    lambda: ScopePlanService(repo, settings).prepare(run_id, body.excluded_clause_ids, body.lifecycle_context.model_dump(mode='json') if body.lifecycle_context is not None else None))

    @app.post('/api/v1/scope-plans/{plan_id}/start', status_code=202)
    def start_scope_plan(plan_id: str, body: VersionMutation, request: Request):
        plan = repo.get('scope_plan', plan_id)
        return idem(request, plan['project_id'], f'start-scope:{plan_id}', body.model_dump(),
                    lambda: ScopePlanService(repo, settings).start(plan_id, body.expected_version))

    @app.get('/api/v1/runs/{run_id}/report/workbook')
    def download_review_workbook(run_id: str):
        content = review_workbook(RunReportService(repo, settings).export(run_id))
        return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="SOC-review-{run_id[:8]}.xlsx"'})

    @app.get('/api/v1/runs/{run_id}/review-feedbacks')
    def review_feedbacks(run_id: str):
        return {'items': ReviewFeedbackService(repo, settings).list_for_run(run_id)}

    @app.get('/api/v1/runs/{run_id}/report')
    def run_report(run_id: str, baseline_run_id: str | None = None):
        service = RunReportService(repo, settings)
        report = service.report(run_id)
        if baseline_run_id:
            report['comparison'] = service.compare(run_id, baseline_run_id)
        return report

    @app.get('/api/v1/runs/{run_id}/report/download')
    def download_run_report(run_id: str):
        return JSONResponse(RunReportService(repo, settings).export(run_id), headers={
            'Content-Disposition': f'attachment; filename="SOC-run-{run_id[:8]}.json"'})

    @app.get('/api/v1/runs/{run_id}/clauses/{clause_id}/trace')
    def clause_trace(run_id: str, clause_id: str):
        return RunReportService(repo, settings).trace(run_id, clause_id)

    @app.post('/api/v1/runs/{run_id}/clauses/{clause_id}/annotations', status_code=201)
    def annotation(run_id: str, clause_id: str, body: AnnotationBody, request: Request):
        run = repo.get('run', run_id)
        return idem(request, run['project_id'], f'annotation:{run_id}:{clause_id}', body.model_dump(),
                    lambda: RunReportService(repo, settings).annotate(run_id, clause_id, body.model_dump()))

    @app.get('/api/v1/jobs/{job_id}')
    def get_job(job_id: str):
        reconcile_interrupted(repo)
        return repo.get('job', job_id)

    @app.get('/api/v1/jobs/{job_id}/events')
    async def events(job_id: str, request: Request):
        after = int(request.headers.get('Last-Event-ID', '0'))
        initial = read_events(repo, job_id, after)
        async def stream():
            nonlocal after
            batch = initial
            heartbeat_at = 0
            while not await request.is_disconnected():
                for event in batch:
                    after = event['event_id']
                    yield f'id: {after}\nevent: {event["type"]}\ndata: {json.dumps(event["data"], ensure_ascii=False)}\n\n'
                if asyncio.get_running_loop().time()-heartbeat_at >= 15:
                    yield 'event: heartbeat\ndata: {}\n\n'
                    heartbeat_at=asyncio.get_running_loop().time()
                await asyncio.sleep(0.5)
                batch = read_events(repo, job_id, after)
        return StreamingResponse(stream(), media_type='text/event-stream')

    @app.post('/api/v1/jobs/{job_id}/cancel')
    def cancel(job_id: str, body: VersionMutation, request: Request):
        job = repo.get('job', job_id)
        return idem(request, job['project_id'], f'cancel:{job_id}', body.model_dump(), lambda: TaskService(repo, settings).cancel(job_id, body.expected_version))

    @app.post('/api/v1/jobs/{job_id}/resume', status_code=202)
    def resume(job_id: str, body: VersionMutation, request: Request):
        job = repo.get('job', job_id)
        return idem(request, job['project_id'], f'resume:{job_id}', body.model_dump(), lambda: TaskService(repo, settings).resume(job_id, body.expected_version))

    @app.post('/api/v1/runs/{run_id}/clauses/{clause_id}/reviews')
    def review(run_id: str, clause_id: str, body: ReviewBody, request: Request):
        run = repo.get('run', run_id)
        return idem(request, run['project_id'], f'review:{run_id}:{clause_id}', body.model_dump(), lambda: ReviewService(repo, settings).apply_review(run_id, clause_id, body.model_dump(exclude_none=True)))

    @app.post('/api/v1/runs/{run_id}/reviews/approve')
    def batch_approve(run_id: str, body: list[dict], request: Request):
        run = repo.get('run', run_id)
        def execute():
            with repo.transaction():
                return [ReviewService(repo, settings).apply_review(run_id, item['clause_id'], {'action': 'approve', 'expected_version': item['expected_version']}) for item in body]
        return idem(request, run['project_id'], f'batch_approve:{run_id}', body, execute)

    @app.get('/api/v1/runs/{run_id}/export-readiness')
    def readiness(run_id: str):
        return ExportService(repo, settings).get_export_readiness(run_id)

    @app.post('/api/v1/runs/{run_id}/exports', status_code=202)
    def export(run_id: str, body: ExportBody, request: Request):
        run = repo.get('run', run_id)
        return idem(request, run['project_id'], f'export:{run_id}', body.model_dump(), lambda: ExportService(repo, settings).create_export(run_id, body.expected_snapshot_hash))

    @app.get('/api/v1/exports/{export_id}')
    def export_detail(export_id: str):
        return {k: v for k, v in repo.get('export', export_id).items() if k != 'managed_path'}

    @app.get('/api/v1/exports/{export_id}/download')
    def export_download(export_id: str):
        return FileResponse(ExportService(repo, settings).download(export_id), filename=f'SOC-{export_id[:8]}.xlsx')

    @app.get('/api/v1/evidence/{evidence_id}')
    def evidence(evidence_id: str):
        from app.services.knowledge import KnowledgeService
        return KnowledgeService.from_settings(repo, settings).evidence(evidence_id)

    @app.get('/api/v1/knowledge/sources')
    def sources(product_id: str | None = None):
        active_ids = {ident for head in repo.list('knowledge_head') for ident in repo.get('knowledge_snapshot',head['snapshot_id'])['source_version_ids']}
        return {'items': [{**{k: v for k, v in s.items() if k != 'managed_path'}, 'active':s['id'] in active_ids} for s in repo.list('source_version', limit=100000) if not product_id or s['product_id'] == product_id], 'errors': repo.list('knowledge_error')}

    @app.get('/api/v1/knowledge/sources/{source_id}')
    def source(source_id: str):
        record = repo.get('source_version', source_id)
        chunks=sorted(repo.list('chunk',parent_id=source_id,limit=100000),key=lambda row:row.get('source_order',0))[:50]
        return {**{k: v for k, v in record.items() if k != 'managed_path'}, 'chunks': chunks}

    @app.post('/api/v1/knowledge/rebuilds', status_code=202)
    def rebuild(body: RebuildBody, request: Request):
        try:
            product = settings.product_id(body.product_id)
        except ValueError as exc:
            raise DomainError('PRODUCT_REQUIRED', '请选择有效产品', 422) from exc
        return idem(request, '', 'knowledge_rebuild', body.model_dump(), lambda: LibraryService(repo,settings).enqueue_legacy(product))

    @app.get('/api/v1/knowledge/snapshots')
    def snapshots():
        return {'items': repo.list('knowledge_snapshot'), 'active': repo.list('knowledge_head')}

    @app.get('/api/v1/knowledge/snapshots/{snapshot_id}')
    def snapshot(snapshot_id: str):
        return repo.get('knowledge_snapshot', snapshot_id)

    @app.get('/api/v1/settings/prompts')
    def prompt_settings():
        return PromptService(repo, settings).get()

    @app.put('/api/v1/settings/prompts')
    def save_prompt_settings(body: PromptSettingsUpdate, request: Request):
        return idem(request, '', 'save_prompts', body.model_dump(),
                    lambda: PromptService(repo, settings).save(body.expected_version, body.prompts))

    @app.get('/api/v1/settings/models')
    def models():
        from app.services.model_settings import view
        return view(settings)

    @app.get('/api/v1/settings/runtime')
    def runtime_settings():
        from app.services.runtime_settings import view
        return view(settings)

    @app.put('/api/v1/settings/runtime')
    def update_runtime_settings(body: dict):
        from app.services.runtime_settings import save
        return save(settings,body)

    @app.get('/api/v1/settings/execution')
    def execution_settings():
        from app.security.execution_policy import ExecutionSettings
        return ExecutionSettings(settings.data_dir).view()

    @app.get('/api/v1/local-model/configuration')
    def local_model_configuration():
        from app.local_runtime.configuration import LocalRuntimeSettings
        return LocalRuntimeSettings(settings).view()

    @app.put('/api/v1/local-model/configuration')
    def save_local_model_configuration(body: dict):
        from app.local_runtime.configuration import LocalRuntimeSettings
        return LocalRuntimeSettings(settings).save(body)

    @app.get('/api/v1/local-model/status')
    def local_model_status():
        from app.local_runtime.configuration import LocalRuntimeSettings
        service = LocalRuntimeSettings(settings)
        affected = [j['id'] for j in repo.list('job', limit=100000) if j['status'] == 'running']
        state = service.manager().status()
        return {**state, 'configuration': service.view(), 'affected_job_ids': affected,
                'generation_verified': bool(state.get('generation_verified'))}

    @app.post('/api/v1/local-model/start')
    def start_local_model(body: dict):
        from app.local_runtime.configuration import LocalRuntimeSettings
        service = LocalRuntimeSettings(settings)
        service.assert_version(body.get('expected_version'))
        return service.manager().start(service.view()['manifest_path'])

    @app.post('/api/v1/local-model/stop')
    def stop_local_model(body: dict):
        from app.local_runtime.configuration import LocalRuntimeSettings
        service = LocalRuntimeSettings(settings)
        service.assert_version(body.get('expected_version'))
        affected = [j['id'] for j in repo.list('job', limit=100000) if j['status'] == 'running']
        if affected:
            raise DomainError('LOCAL_MODEL_BUSY', '有后台任务正在运行，请先取消或等待完成', 409, {'job_ids': affected})
        return service.manager().stop()

    @app.put('/api/v1/settings/execution')
    def save_execution_settings(body: dict):
        from app.security.execution_policy import ExecutionSettings
        return ExecutionSettings(settings.data_dir).save(body)

    @app.post('/api/v1/settings/models/check')
    def check_model(body: dict):
        from app.model_gateway.endpoints import check_model_endpoint
        return check_model_endpoint(settings,body.get('role'),body.get('profile_id'),mode=body.get('mode','models'))

    @app.put('/api/v1/settings/models')
    def save_models(body: dict):
        from app.services.model_settings import save
        return save(settings,body)

    from app.telemetry.api import install_telemetry_routes
    install_telemetry_routes(app, repo, settings, idem)
    from app.evaluation.api import install_evaluation_routes
    install_evaluation_routes(app, repo, settings, idem)
    from app.api.libraries import create_router
    app.include_router(create_router(repo,settings))
    from app.services.memories_api import install_memory_routes
    install_memory_routes(app,repo,settings,idem)
    from app.api.review_workbench import create_router as review_router
    from app.api.document_tasks import create_router as document_router
    from app.api.context_management import create_router as context_router
    app.include_router(review_router(repo, settings))
    app.include_router(document_router(repo, settings))
    app.include_router(context_router(repo, settings))
    built = settings.root / 'frontend' / 'dist'
    if built.is_dir():
        app.mount('/', StaticFiles(directory=built, html=True), name='frontend')
    return app
