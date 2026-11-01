from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter, column_index_from_string
from app.schemas.excel import MappingSelection
from app.excel.precheck import precheck
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash, file_hash
from app.excel.requirements import build_requirement_clauses


def create_mapping(path, selection):
    choice = MappingSelection.model_validate(selection)
    report = precheck(path)
    if not report['accepted']:
        raise DomainError('WORKBOOK_UNSUPPORTED', '工作簿含未支持结构', 415, report)
    if any(row <= choice.header_row for row in choice.rows):
        raise DomainError('MAPPING_INVALID', '输入和三个输出列不能重叠，数据行必须在表头之后', 422)
    digest = file_hash(path)
    book = load_workbook(path, data_only=False)
    clauses, preview, targets, skipped, excluded = [], [], [], [], []
    header_targets = {}
    try:
        if choice.sheet not in book.sheetnames:
            raise DomainError('SHEET_NOT_FOUND', '工作表不存在', 422)
        sheet = book[choice.sheet]
        if choice.output_mode == 'append':
            if choice.header_row > sheet.max_row or not any(c.value is not None for c in sheet[choice.header_row]):
                raise DomainError('HEADER_REQUIRED', '请选择实际表头行；无表头请先补充表头再上传', 422)
            last = max(sheet.max_column, max((r.max_col for r in sheet.merged_cells.ranges), default=0),
                       max((d.max or column_index_from_string(k) for k, d in sheet.column_dimensions.items()
                            if not (not d.hidden and d.max == 16384 and (d.min or column_index_from_string(k)) < 16384
                                    and (d.min or column_index_from_string(k)) > sheet.max_column)), default=0))
            if last + 3 > 16384:
                raise DomainError('OUTPUT_COLUMNS_UNAVAILABLE', '原表右侧没有足够空间新增三列', 422)
            choice.decision_column, choice.response_column, choice.citation_column = [get_column_letter(last+i) for i in (1,2,3)]
            header_targets = {choice.sheet: {f'{column}{choice.header_row}': title for column, title in zip(
                [choice.decision_column, choice.response_column, choice.citation_column], ['SOC 结论', 'SOC 英文应答', 'SOC 引用'])}}
        columns = [choice.input_column, choice.decision_column, choice.response_column, choice.citation_column]
        if len(set(columns)) != 4:
            raise DomainError('MAPPING_INVALID', '输入和三个输出列不能重叠', 422)
        if any(row > sheet.max_row for row in choice.rows):
            raise DomainError('MAPPING_INVALID', '技术行范围超出工作表现有行，请调整范围', 422, {'sheet': choice.sheet, 'max_row': sheet.max_row})
        for row in choice.rows:
            source = sheet[f'{choice.input_column}{row}']
            location = {'sheet': choice.sheet, 'cell': source.coordinate}
            if source.data_type == 'f':
                raise DomainError('INPUT_CELL_INVALID', '原文单元格含公式，请选择原始条款文本', 422, location)
            if source.value is None or not str(source.value).strip():
                if not choice.skip_empty_rows:
                    raise DomainError('INPUT_CELL_INVALID', '原文单元格为空；请调整范围或勾选跳过空行', 422, location)
                skipped.append({**location, 'row': row, 'reason': 'MERGED_CONTINUATION' if isinstance(source, MergedCell) else 'EMPTY_INPUT'})
                continue
            if len(str(source.value)) > 12000:
                raise DomainError('CLAUSE_TOO_LONG', '原文过长，请拆分或澄清后重新映射', 422, location)
            if row in choice.excluded_rows:
                coord = {'sheet': choice.sheet, 'row': row, 'input_cell': source.coordinate, 'outputs': {}}
                excluded.append({'clause_id': content_hash({'file': digest, 'sheet': choice.sheet, 'row': row, 'text': str(source.value)}),
                                 'raw_text': str(source.value), 'coordinate': coord, 'source': 'upload_selection'})
                continue
            for column in columns:
                coordinate = f'{column}{row}'
                # openpyxl already identifies non-anchor cells; avoid scanning every merge for every cell.
                if isinstance(sheet[coordinate], MergedCell):
                    merged = next(r for r in sheet.merged_cells.ranges if coordinate in r)
                    raise DomainError('MERGED_TARGET_AMBIGUOUS', '映射涉及非锚点合并单元格', 422, {'sheet': choice.sheet, 'cell': coordinate, 'range': str(merged)})
            outputs = {'decision': f'{choice.decision_column}{row}', 'response_en': f'{choice.response_column}{row}', 'citations': f'{choice.citation_column}{row}'}
            for coordinate in outputs.values():
                cell = sheet[coordinate]
                if cell.data_type == 'f':
                    raise DomainError('TARGET_FORMULA', '输出区域含公式，不能覆盖', 422, {'sheet': choice.sheet, 'cell': coordinate})
                if cell.value is not None:
                    preview.append({'sheet': choice.sheet, 'cell': coordinate, 'existing_value': str(cell.value)})
                targets.append({'sheet': choice.sheet, 'cell': coordinate})
            clauses.append({'raw_text': str(source.value), 'coordinate': {'sheet': choice.sheet, 'row': row, 'input_cell': source.coordinate, 'outputs': outputs}})
    finally:
        book.close()
    if not clauses:
        raise DomainError('INPUT_RANGE_EMPTY', '所选行的原文列均为空，请调整原文列或技术行范围', 422, {'skipped_rows': skipped})
    if preview and not choice.confirm_overwrite:
        raise DomainError('OVERWRITE_CONFIRM_REQUIRED', '以下输出单元格已有内容，请确认覆盖范围', 409, {'overwrite_preview': preview})
    clauses = build_requirement_clauses(path, clauses, choice.model_dump(), excluded=excluded)
    payload = {'selection': choice.model_dump(), 'clauses': clauses, 'excluded': excluded, 'header_targets': header_targets, 'overwrite_preview': preview, 'target_preview': targets, 'skipped_rows': skipped,
               'file_sha256': digest, 'capability_version': report['capability_version']}
    return {**payload, 'content_hash': content_hash(payload)}
