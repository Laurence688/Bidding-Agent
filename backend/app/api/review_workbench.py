from fastapi import APIRouter,Query
from app.services.review_workbench import ReviewWorkbench

def create_router(repo,settings):
    router=APIRouter(prefix='/api/v1');service=ReviewWorkbench(repo,settings)
    @router.get('/projects/{project_id}/review-workbench')
    def queue(project_id:str,task_id:str|None=None,product_id:str|None=None,decision:str|None=None,review_status:str='pending',cursor:str|None=None,limit:int=Query(25,ge=1,le=200),run_id:str|None=None):
        return service.list(project_id,task_id,product_id,decision,review_status,cursor,limit,run_id)
    @router.get('/runs/{run_id}/review-items/{clause_id}')
    def detail(run_id:str,clause_id:str):return service.detail(run_id,clause_id)
    @router.get('/runs/{run_id}/execution-summary')
    def execution(run_id:str,clause_id:str|None=None):return service.execution(run_id,clause_id)
    @router.get('/projects/{project_id}/execution-logs')
    def logs(project_id:str,mode:str|None=None,cursor:str|None=None,limit:int=Query(25,ge=1,le=200),category:str|None=None):
        return service.logs(project_id,mode,cursor,limit,category)
    return router
