from fastapi import APIRouter,Request,UploadFile,File,Form,Query
from pydantic import Field
from typing import Literal
from app.schemas.core import StrictModel
from app.services.libraries import LibraryService
from app.domain.errors import DomainError

class ProductCreate(StrictModel):
    id:str=Field(default="",max_length=80)
    family:str=Field(default="",max_length=100)
    name:str=Field(default='',max_length=100)
    aliases:list[str]=[]
class LibraryCreate(StrictModel):
    name:str=Field(min_length=1,max_length=100)
    product_id:str
    description:str=Field(default='',max_length=2000)
    wizard_draft:dict=Field(default_factory=dict)
class LibraryPatch(StrictModel):
    expected_version:int=Field(ge=1,strict=True)
    name:str|None=Field(default=None,min_length=1,max_length=100)
    description:str|None=Field(default=None,max_length=2000)
    wizard_draft:dict|None=None
    enabled:bool|None=None
class DocumentPatch(StrictModel):
    expected_version:int=Field(ge=1,strict=True)
    enabled:bool|None=None
    rule_draft:dict|None=None
class ProductPatch(StrictModel):
    family:str=Field(default="",max_length=100)
    expected_version:int=Field(ge=0,strict=True)
    name:str=Field(min_length=1,max_length=100)
    aliases:list[str]=Field(default_factory=list)
class TemplateCreate(StrictModel):
    name:str=Field(min_length=1,max_length=100)
    config:dict
class TemplatePatch(StrictModel):
    expected_version:int=Field(ge=1,strict=True)
    name:str|None=Field(default=None,min_length=1,max_length=100)
    config:dict|None=None
class Toggle(StrictModel):
    expected_version:int=Field(ge=1,strict=True)
    enabled:bool
class Bindings(StrictModel):
    library_ids:list[str]
    expected_version:int|None=Field(default=None,ge=0,strict=True)
class LibraryProjects(StrictModel):
    expected_version:str=Field(min_length=1)
    project_ids:list[str]
class Preview(StrictModel):
    strategy:Literal['default','excel','leaf_sections','paragraphs','fixed','delimiter','excel_records','whole_document']='default'
    max_chars:int=Field(default=1000,ge=100,le=12000)
    selection:dict|None=None
    overlap:int=Field(default=0,ge=0,le=11999)
    delimiter:str=Field(default='',max_length=1000)
    template_id:str|None=None
    template_version:int|None=Field(default=None,ge=1)
    embedding_profile_id:str='local-default'
class Build(StrictModel):
    preview_ids:list[str]
    embedding_profile_id:str='local-default'
class Version(StrictModel):expected_version:int=Field(ge=1,strict=True)
class Purge(Version):confirmation_name:str=Field(min_length=1,max_length=100)
class Search(StrictModel):
    build_id:str|None=None
    query:str=Field(min_length=1,max_length=12000)
    project_id:str|None=None
class Profile(StrictModel):
    name:str=Field(min_length=1,max_length=100)
    mode:str
    model:str=Field(min_length=1,max_length=1024)
    base_url:str=''
    api_key:str=Field(default='',max_length=4096)
    max_chars:int=Field(default=1000,ge=100,le=12000)
    max_length:int=Field(default=1024,ge=128,le=32768)
    batch_size:int=Field(default=4,ge=1,le=64)
class Check(StrictModel):allow_cloud:bool=False

def create_router(repo,settings):
    router=APIRouter(prefix='/api/v1/kb');service=LibraryService(repo,settings)
    def idem(request,operation,body,action):
        # A request secret is never stored in the generic idempotency ledger.
        from app.domain.identifiers import content_hash
        safe={k:v for k,v in body.items() if k!='api_key'}
        if body.get('api_key'):safe['credential_hash']=content_hash(body['api_key'])
        return repo.idempotent('',operation,request.headers.get('Idempotency-Key'),safe,lambda db:action())
    @router.get('/products')
    def products():return {'items':service.products()}
    @router.post('/products',status_code=201)
    def product(body:ProductCreate,request:Request):return idem(request,'kb-product',body.model_dump(),lambda:service.create_product(body.model_dump()))
    @router.patch('/products/{ident}')
    def edit_product(ident:str,body:ProductPatch):return service.update_product(ident,body.model_dump(exclude_unset=True))
    @router.get('/split-templates')
    def templates():return {'items':service.templates()}
    @router.post('/split-templates',status_code=201)
    def template(body:TemplateCreate,request:Request):return idem(request,'kb-template',body.model_dump(),lambda:service.create_template(body.model_dump()))
    @router.patch('/split-templates/{ident}')
    def edit_template(ident:str,body:TemplatePatch):return service.update_template(ident,body.model_dump(exclude_unset=True,exclude_none=True))
    @router.delete('/split-templates/{ident}')
    def delete_template(ident:str,expected_version:int=Query(ge=1)):return service.delete_template(ident,expected_version)
    @router.get('/libraries')
    def libraries():return {'items':service.libraries()}
    @router.post('/libraries',status_code=201)
    def library(body:LibraryCreate,request:Request):return idem(request,'kb-library',body.model_dump(),lambda:service.create_library(body.model_dump()))
    @router.get('/libraries/{ident}')
    def detail(ident:str):
        result=service.detail(ident)
        result['embedding_profiles']=[service.public_profile(p) for p in result['embedding_profiles']]
        return result
    @router.patch('/libraries/{ident}')
    def toggle_library(ident:str,body:LibraryPatch):return service.patch_library(ident,body.model_dump(exclude_unset=True,exclude_none=True))
    @router.post('/libraries/{ident}/trash')
    def trash_library(ident:str,body:Version):return service.trash(ident,body.expected_version)
    @router.post('/libraries/{ident}/restore')
    def restore_library(ident:str,body:Version):return service.restore(ident,body.expected_version)
    @router.delete('/libraries/{ident}')
    def purge_library(ident:str,body:Purge):return service.purge(ident,body.expected_version,body.confirmation_name)
    @router.get('/libraries/{ident}/projects')
    def library_projects(ident:str):return service.library_projects(ident)
    @router.patch('/libraries/{ident}/projects')
    def library_projects_patch(ident:str,body:LibraryProjects):return service.bind_library_projects(ident,body.project_ids,body.expected_version)
    @router.post('/libraries/{ident}/draft/discard')
    def discard_draft(ident:str,body:Version):return service.discard_draft(ident,body.expected_version)
    @router.get('/projects/{ident}/bindings')
    def bindings(ident:str):return service.bindings(ident)
    @router.put('/projects/{ident}/bindings')
    def bind(ident:str,body:Bindings):return service.bind(ident,body.library_ids,body.expected_version)
    @router.post('/libraries/{ident}/documents',status_code=201)
    async def upload(ident:str,request:Request,file:UploadFile=File(...),document_type:str=Form('product_description')):
        content=await file.read(50*1024*1024+1)
        from hashlib import sha256
        key=request.headers.get('Idempotency-Key')
        if key:return idem(request,'kb-upload:'+ident,{'name':file.filename or '','content_hash':sha256(content).hexdigest(),'document_type':document_type},lambda:service.upload(ident,file.filename or '',content,document_type))
        return service.upload(ident,file.filename or '',content,document_type)
    @router.patch('/documents/{ident}')
    def toggle_document(ident:str,body:DocumentPatch):return service.patch_document(ident,body.model_dump(exclude_unset=True,exclude_none=True))
    @router.post('/documents/{ident}/preview',status_code=201)
    def preview(ident:str,body:Preview):return service.preview(ident,body.model_dump())
    @router.get('/documents/{ident}/metadata')
    def metadata(ident:str,header_row:int=Query(default=1,ge=1,le=10000)):return service.document_metadata(ident,header_row)
    @router.post('/libraries/{ident}/builds',status_code=202)
    def build(ident:str,body:Build,request:Request):return idem(request,'kb-build:'+ident,body.model_dump(),lambda:service.enqueue_build(ident,body.model_dump()))
    @router.get('/builds/{ident}')
    def get_build(ident:str):
        return service.build_view(repo.get('kb_build',ident))
    @router.post('/builds/{ident}/publish')
    def publish(ident:str,body:Version):return service.publish(ident,body.expected_version)
    @router.post('/builds/{ident}/cancel')
    def cancel(ident:str,body:Version):return service.cancel_build(ident,body.expected_version)
    @router.post('/libraries/{ident}/search')
    def search(ident:str,body:Search):return service.search(ident,body.query,body.project_id,body.build_id)
    @router.get('/embedding-profiles')
    def profiles():return {'items':[service.public_profile(p) for p in service.profiles()]}
    @router.post('/embedding-profiles',status_code=201)
    def profile(body:Profile,request:Request):return idem(request,'kb-embedding-profile',body.model_dump(),lambda:service.create_profile(body.model_dump()))
    @router.post('/embedding-profiles/{ident}/check')
    def check(ident:str,body:Check):return service.check_profile(ident,body.allow_cloud)
    return router
