from fastapi import APIRouter, Request, Query
from pydantic import Field
from typing import Literal
from app.schemas.core import StrictModel
from app.services.context_management import ContextService


class CompressBody(StrictModel):
    product_id:str=Field(min_length=1,max_length=100)


def create_router(repo,settings):
    router=APIRouter(prefix='/api/v1');service=ContextService(repo,settings)

    @router.get('/sessions/{session_id}/context')
    def preview(session_id:str,product_id:str,raw_text:str=Query(default='',max_length=12000),mode:Literal['PRODUCT_QA','SOC','SOC_SINGLE','SOC_BATCH','TRANSLATION']='PRODUCT_QA',target_language:Literal['zh','en']='zh',reference_message_id:str|None=None,reference_clause_id:str|None=None):
        return service.preview(session_id,product_id,raw_text,mode,target_language,reference_message_id,reference_clause_id)

    @router.get('/runs/{run_id}/activity')
    def activity(run_id:str):
        from app.services.activity import ActivityService
        return ActivityService(repo).snapshot(run_id)

    @router.get('/runs/{run_id}/context')
    def run_context(run_id:str):return service.run_view(run_id)

    @router.post('/sessions/{session_id}/context/compress',status_code=202)
    def compress(session_id:str,body:CompressBody,request:Request):
        return service.enqueue_compression(session_id,body.product_id,request.headers.get('Idempotency-Key'))

    return router
