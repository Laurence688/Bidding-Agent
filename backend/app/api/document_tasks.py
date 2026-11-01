from fastapi import APIRouter, Request, File, UploadFile
from fastapi.responses import FileResponse
from app.schemas.document_tasks import DocumentTaskCreate
from app.services.document_tasks import DocumentTaskService


def create_router(repo, settings):
    router = APIRouter(prefix='/api/v1/document-tasks')
    service = DocumentTaskService(repo, settings)

    @router.post('/projects/{project_id}/files', status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...)):
        content = await file.read(50 * 1024 * 1024 + 1)
        return service.upload(project_id, file.filename or '', content)

    @router.get('/projects/{project_id}/files/{file_id}/preview')
    def preview(project_id: str, file_id: str):
        return service.preview(project_id, file_id)

    @router.post('/projects/{project_id}/tasks', status_code=202)
    def create(project_id: str, body: DocumentTaskCreate, request: Request):
        return service.enqueue(project_id, body.model_dump(), request.headers.get('Idempotency-Key'))

    @router.get('/projects/{project_id}/tasks')
    def tasks(project_id: str):
        return service.list_tasks(project_id)

    @router.get('/runs/{run_id}')
    def run(run_id: str):
        return service.detail(run_id)

    @router.post('/runs/{run_id}/{action}')
    def action(run_id: str, action: str, request: Request):
        run = service._run(run_id)
        return repo.idempotent(run['project_id'], 'document-action:' + run_id + ':' + action,
            request.headers.get('Idempotency-Key'), {'action': action}, lambda db: service.action(run_id, action))

    @router.get('/artifacts/{artifact_id}/download')
    def download(artifact_id: str):
        artifact = repo.get('document_artifact', artifact_id)
        return FileResponse(service.artifact_path(artifact_id), filename=artifact['name'])

    return router
