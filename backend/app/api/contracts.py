from typing import Literal, Any
from pydantic import Field
from app.schemas.core import StrictModel, LifecycleContext


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    default_product_id: str


class VersionMutation(StrictModel):
    expected_version: int = Field(ge=1)


class PromptSettingsUpdate(StrictModel):
    expected_version: int = Field(ge=0, strict=True)
    prompts: dict[str, str]


class ProjectPatch(VersionMutation):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    default_product_id: str | None = None


class SessionCreate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class SessionPatch(VersionMutation):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    pinned: bool | None = Field(default=None, strict=True)


class SessionCopy(VersionMutation):
    target_project_id: str = Field(min_length=1,max_length=100)


class SessionDelete(VersionMutation):
    cancel_running: bool = False


class TaskCreate(StrictModel):
    reference_message_id: str | None = Field(default=None,max_length=100)
    reference_clause_id: str | None = Field(default=None,max_length=100)
    mode: Literal['PRODUCT_QA', 'SOC_SINGLE', 'SOC_BATCH']
    model_mode: Literal['local', 'cloud'] | None = None
    product_id: str
    session_id: str | None = None
    raw_text: str | None = Field(default=None, max_length=12000)
    mapping_id: str | None = None
    task_note: str = Field(default='', max_length=12000)


class FileReference(StrictModel):
    session_id: str | None = None
    product_id: str | None = None
    task_note: str = Field(default='', max_length=12000)
    file_id: str


class NewRun(VersionMutation):
    base_run_id: str
    changes: dict[str, str] = Field(default_factory=dict)
    product_id: str | None = None
    reason: str = '用户重新运行'


class ScopePlanBody(StrictModel):
    excluded_clause_ids: list[str] | None = None
    lifecycle_context: LifecycleContext | None = None


class ReviewBody(VersionMutation):
    action: Literal['comment', 'edit', 'approve', 'revoke']
    edits: dict[str, Any] | None = None
    comments: str | None = None


class AnnotationBody(StrictModel):
    expected_version: int = Field(ge=0)
    result_id: str
    verdict: Literal['correct', 'incorrect', 'uncertain', 'not_requirement']
    expected_decision: Literal['FC', 'PC', 'NC', 'UNKNOWN'] | None = None
    stage: Literal['input', 'translate', 'split_claims', 'retrieve', 'admit_evidence', 'judge_claim', 'verify_citations', 'verify_semantic_support', 'render_response', 'validate_output', 'model', 'other'] | None = None
    notes: str = Field(default='', max_length=4000)


class ExportBody(StrictModel):
    expected_snapshot_hash: str


class RebuildBody(StrictModel):
    product_id: str



class CardSelection(VersionMutation):
    selection: dict[str, Any] | None = None


class CardConfirm(VersionMutation):
    mapping_id: str
