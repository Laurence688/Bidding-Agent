from fastapi import Request, Query
from pydantic import Field
from app.schemas.core import StrictModel
from app.services.memories import MemoryManagementService

class MemoryStatus(StrictModel):
    expected_version:int=Field(ge=1,strict=True)
    enabled:bool=Field(strict=True)

def install_memory_routes(app,repo,settings,idem):
    service=MemoryManagementService(repo,settings)
    @app.get('/api/v1/projects/{project_id}/memories')
    def memories(project_id:str,q:str=Query(default='',max_length=200)):
        return service.list(project_id,q)
    @app.get('/api/v1/projects/{project_id}/memories/{memory_id}')
    def memory(project_id:str,memory_id:str):
        return service.get(project_id,memory_id)
    @app.put('/api/v1/projects/{project_id}/memories/{memory_id}/status')
    def status(project_id:str,memory_id:str,body:MemoryStatus,request:Request):
        return idem(request,project_id,'memory-status:'+memory_id,body.model_dump(),lambda:service.set_enabled(project_id,memory_id,body.expected_version,body.enabled))
