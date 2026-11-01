from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.schemas.core import StrictModel


class MappingSelection(StrictModel):
    sheet: str
    header_row: int = Field(ge=1, le=1048576)
    input_column: str
    decision_column: str | None = None
    response_column: str | None = None
    citation_column: str | None = None
    output_mode: Literal['mapped', 'append'] = 'mapped'
    requirement_value_columns: list[str] | None = None
    excluded_rows: list[int] = Field(default_factory=list, max_length=10000)
    rows: list[int] = Field(min_length=1, max_length=10000)
    confirm_overwrite: bool = False
    skip_empty_rows: bool = True

    @field_validator('input_column', 'decision_column', 'response_column', 'citation_column')
    @classmethod
    def column(cls, value):
        from openpyxl.utils import column_index_from_string
        if value is None:
            return None
        value = value.upper()
        if not 1 <= column_index_from_string(value) <= 16384:
            raise ValueError('列超出 Excel 范围')
        return value

    @field_validator('rows')
    @classmethod
    def valid_rows(cls, value):
        if len(value) != len(set(value)) or any(r < 1 or r > 1048576 for r in value):
            raise ValueError('行范围重复或越界')
        return sorted(value)

    @field_validator('requirement_value_columns')
    @classmethod
    def value_columns(cls, value):
        if value is None:
            return None
        values = [cls.column(v) for v in value]
        if len(set(values)) != len(values):
            raise ValueError('要求值列重复')
        return values

    @model_validator(mode='after')
    def validate_modes(self):
        if self.output_mode == 'mapped' and not all([self.decision_column, self.response_column, self.citation_column]):
            raise ValueError('请选择三个输出列')
        if len(set(self.excluded_rows)) != len(self.excluded_rows) or not set(self.excluded_rows).issubset(self.rows):
            raise ValueError('排除行必须属于所选范围且不能重复')
        return self
