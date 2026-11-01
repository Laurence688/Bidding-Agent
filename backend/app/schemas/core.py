from enum import Enum
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.domain.identifiers import new_id


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ClauseDecision(str, Enum):
    FC = 'FC'
    PC = 'PC'
    NC = 'NC'
    UNKNOWN = 'UNKNOWN'


class ClaimVerdict(str, Enum):
    SUPPORTED = 'SUPPORTED'
    CONTRADICTED = 'CONTRADICTED'
    UNKNOWN = 'UNKNOWN'


class TaskInput(StrictModel):
    mode: Literal['PRODUCT_QA', 'SOC_SINGLE', 'SOC_BATCH', 'EVALUATION']
    project_id: str
    session_id: str | None = None
    product_id: str
    input_snapshot_id: str


class InputSnapshot(StrictModel):
    id: str = Field(default_factory=new_id)
    raw_text: str = ''
    file_id: str | None = None
    file_sha256: str | None = None
    mapping_version: int | None = None
    mapping_id: str | None = None
    clauses: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str


class SourceSpan(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Claim(StrictModel):
    id: str = Field(default_factory=new_id)
    clause_id: str
    text: str = Field(min_length=1)
    source_spans: list[SourceSpan]
    relation: Literal['REQUIRED', 'OR'] = 'REQUIRED'
    alternatives: list[str] = Field(default_factory=list)
    alternative_groups: list[list[str]] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    project_fact: bool = False


class LocatorSegment(StrictModel):
    kind: Literal['docx', 'pdf', 'xlsx', 'paragraph', 'table_row', 'comment']
    section: str | None = None
    paragraph: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    row_start: int | None = None
    comment_id: str | None = None
    table: int | None = None
    row: int | None = None
    physical_page: int | None = None
    printed_page: str | None = None
    sheet: str | None = None
    cells: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    page_source: str | None = None
    rendered_pdf: str | None = None
    section_path: list[str] = Field(default_factory=list)
    row_end: int | None = None
    part: int | None = None


class Locator(StrictModel):
    kind: Literal['docx', 'pdf', 'xlsx']
    section: str | None = None
    paragraph: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    segments: list[LocatorSegment] = Field(default_factory=list)
    table: int | None = None
    row: int | None = None
    physical_page: int | None = None
    printed_page: str | None = None
    sheet: str | None = None
    cells: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    page_source: str | None = None
    rendered_pdf: str | None = None
    section_path: list[str] = Field(default_factory=list)
    row_end: int | None = None
    part: int | None = None


class EvidenceRef(StrictModel):
    id: str
    source_version_id: str
    chunk_id: str
    locator: Locator
    quote: str
    product_id: str
    document_type: str = 'product_description'
    version_label: str = '未标注'
    source_name: str = ''


class ParameterValue(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    actual_value: str = Field(min_length=1, max_length=160)
    unit: str = Field(default='', max_length=80)
    conditions: str = Field(default='', max_length=400)
    evidence_chunk_id: str


class ClaimAssessment(StrictModel):
    claim_id: str
    verdict: ClaimVerdict
    reason_code: str
    explanation: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    parameter_values: list[ParameterValue] = Field(default_factory=list)
    gap_type: Literal['none', 'evidence', 'requirement_input', 'project_fact', 'scope', 'conflict'] = 'evidence'


class CitationCheck(StrictModel):
    ref_id: str
    locatable: bool
    literal_match: bool
    applicable: bool
    semantic_support: Literal['SUPPORTED', 'UNSUPPORTED', 'UNKNOWN'] = 'UNKNOWN'


class ClauseResult(StrictModel):
    clause_id: str
    version: int = 1
    decision: ClauseDecision
    raw_text: str
    translation_zh: str = ''
    claims: list[Claim] = Field(default_factory=list)
    assessments: list[ClaimAssessment] = Field(default_factory=list)
    response_en: str | None = None
    checks: list[CitationCheck] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    coverage_complete: bool = False
    output_valid: bool = False
    candidates: list[EvidenceRef] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def no_unknown_deliverable(self):
        if self.decision == ClauseDecision.UNKNOWN and self.response_en:
            raise ValueError('UNKNOWN 不得带正式英文口径')
        return self


class ReviewMutation(StrictModel):
    run_id: str
    clause_id: str
    expected_version: int = Field(ge=1)
    action: Literal['comment', 'edit', 'approve', 'revoke']
    edits: dict[str, Any] | None = None
    comments: str | None = None


class ExportReadiness(StrictModel):
    eligible: bool
    blocker_counts: dict[str, int]
    blocker_items: list[dict[str, Any]]
    snapshot_hash: str


class UsageAttempt(StrictModel):
    attempt_id: str
    provider: str
    model: str
    tokens: dict[str, int | None] | None = None
    cache_fields: dict[str, int | None] | None = None
    latency: float | None = None
    completeness: Literal['complete', 'incomplete'] = 'incomplete'


class LifecycleContext(StrictModel):
    reference_date: date | None = None
    exact_model: str | None = Field(default=None, max_length=80)
    source_version_id: str | None = None


class RunContext(StrictModel):
    run_id: str
    mode: Literal['PRODUCT_QA', 'SOC_SINGLE', 'SOC_BATCH', 'EVALUATION']
    local_user_id: str
    project_id: str
    product_id: str
    config_snapshot_id: str
    knowledge_snapshot_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    evaluation_run_id: str | None = None
    job_id: str | None = None
    source_context: dict[str, Any] = Field(default_factory=dict)
    lifecycle_context: LifecycleContext = Field(default_factory=LifecycleContext)


class JobProgress(StrictModel):
    stage: str
    processed_items: int = 0
    total_items: int | None = None
    current_node: str | None = None


class KnowledgeSnapshot(StrictModel):
    id: str
    product_id: str
    source_version_ids: list[str]
    parser_fingerprint: str
    embedding_fingerprint: str | None = None
    status: str
    errors: list[dict] = Field(default_factory=list)


class EvidenceAdmission(StrictModel):
    admitted: list[EvidenceRef]
    excluded: list[EvidenceRef]
    reason_codes: list[str]


class QAResult(StrictModel):
    answer: str
    evidence_refs: list[EvidenceRef]
    uncertainties: list[str]
