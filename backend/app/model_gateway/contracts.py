from dataclasses import dataclass, field
from typing import Type
from pydantic import BaseModel


@dataclass
class ModelRequest:
    run_id: str
    clause_id: str
    claim_id: str
    logical_node_name: str
    messages: list[dict]
    output_model: Type[BaseModel]
    config_version: str
    call_kind: str = 'initial'
    max_attempts: int | None = None
    operation_budget_id: str | None = None
    operation_max_attempts: int | None = None
    max_output_tokens: int | None = None


@dataclass
class ModelResult:
    payload: dict | None = None
    error_code: str | None = None
    attempt_ids: list[str] = field(default_factory=list)
    stop_reason: str | None = None


class ProviderFailure(Exception):
    def __init__(self, code, transient=False, local_context=None):
        self.code, self.transient = code, transient
        self.local_context = local_context
        super().__init__(code)
