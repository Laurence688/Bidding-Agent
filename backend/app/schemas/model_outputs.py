from typing import Literal
from pydantic import Field
from app.schemas.core import StrictModel, SourceSpan, ParameterValue


class TranslationOutput(StrictModel):
    translation_zh: str
    ambiguous: bool


class SplitClaim(StrictModel):
    text: str = Field(min_length=1)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    relation: Literal['REQUIRED', 'OR']
    alternatives: list[str]
    alternative_groups: list[list[str]] = Field(default_factory=list)
    conditions: list[str]
    numbers: list[str]
    project_fact: bool


class SplitOutput(StrictModel):
    claims: list[SplitClaim] = Field(min_length=1, max_length=100)


class CitationSelection(StrictModel):
    chunk_id: str
    quote: str


class JudgeOutput(StrictModel):
    verdict: Literal['SUPPORTED', 'CONTRADICTED', 'UNKNOWN']
    reason_code: str
    explanation: str
    citations: list[CitationSelection]
    parameter_values: list[ParameterValue] = Field(default_factory=list)
    gap_type: Literal['none', 'evidence', 'requirement_input', 'project_fact', 'scope', 'conflict'] = 'evidence'


class SemanticOutput(StrictModel):
    support: Literal['SUPPORTED', 'UNSUPPORTED', 'UNKNOWN']
    reason: str


class RenderOutput(StrictModel):
    response_en: str
    addressed_claim_ids: list[str]
    gap_sentences: dict[str, str]


class QAOutput(StrictModel):
    answer: str
    citations: list[CitationSelection]
    uncertainties: list[str]
