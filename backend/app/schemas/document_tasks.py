from typing import Literal
from pydantic import Field
from app.schemas.core import StrictModel

CATEGORIES = ('项目概况', '资格与材料', '时间节点', '技术要求', '商务与交付', '评分条款', '重点关注')


class ExcelScope(StrictModel):
    sheet: str = Field(min_length=1)
    range: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1)


class DocumentTaskCreate(StrictModel):
    mode: Literal['TRANSLATION', 'TENDER_ANALYSIS']
    raw_text: str = Field(default='', max_length=2000000)
    file_ids: list[str] = Field(default_factory=list, max_length=50)
    target_language: Literal['zh', 'en'] = 'zh'
    excel_selections: dict[str, ExcelScope | list[ExcelScope]] = Field(default_factory=dict)
    confirmed: bool = False
    session_id: str | None = Field(default=None,max_length=100)
    product_id: str | None = Field(default=None,max_length=100)
    reference_clause_id: str | None = Field(default=None,max_length=100)
    reference_message_id: str | None = Field(default=None,max_length=100)


class TranslationResult(StrictModel):
    translation: str = Field(min_length=1)


class AnalysisItem(StrictModel):
    category: Literal['项目概况', '资格与材料', '时间节点', '技术要求', '商务与交付', '评分条款', '重点关注']
    summary: str = Field(min_length=1, max_length=8000)
    quote: str = Field(min_length=1, max_length=16000)


class AnalysisResult(StrictModel):
    items: list[AnalysisItem] = Field(max_length=100)
