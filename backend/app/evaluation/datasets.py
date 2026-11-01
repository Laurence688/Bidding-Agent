from typing import Literal
from pydantic import Field
from app.schemas.core import StrictModel, LifecycleContext
from app.domain.identifiers import new_id
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash


class Gold(StrictModel):
    decision: Literal['FC', 'PC', 'NC', 'UNKNOWN']
    claims: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    case_type: str = 'manual'
    notes: str = ''
    parameters: list[dict] = Field(default_factory=list)


class EvaluationCase(StrictModel):
    input_gaps: list[str] = Field(default_factory=list)
    case_id: str = Field(default_factory=new_id, min_length=1, max_length=100)
    source_context: dict = Field(default_factory=dict)
    lifecycle_context: LifecycleContext = Field(default_factory=LifecycleContext)
    product_id: Literal['M6000-SE', '6120H-S']
    raw_text: str = Field(min_length=1, max_length=12000)
    gold: Gold


def execution_input(case):
    # 手工 Claim、解释、证据及预期判定绝不进入执行器的数据白名单。
    return {key: case[key] for key in ('product_id', 'raw_text', 'source_context', 'lifecycle_context') if key in case}


class DatasetService:
    def __init__(self, repo):
        self.repo = repo

    def create(self, name):
        return self.repo.put('evaluation_dataset', {'name': name, 'active_version_id': None})

    def add_cases(self, dataset_id, cases):
        self.repo.get('evaluation_dataset', dataset_id)
        payload = [EvaluationCase.model_validate({**case, 'input_gaps': case.get('input_gaps',
            [k for k in ('source_context', 'lifecycle_context') if k not in case])}).model_dump(mode='json') for case in cases]
        if len({c['case_id'] for c in payload}) != len(payload):
            raise DomainError('DUPLICATE_CASE_ID', '案例 ID 不能重复', 422)
        with self.repo.transaction():
            version = self.repo.put('dataset_version', {'cases': payload, 'content_hash': content_hash(payload)}, parent_id=dataset_id)
            self.repo.update('evaluation_dataset', dataset_id, {'active_version_id': version['id']})
        return version
