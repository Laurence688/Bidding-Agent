"""Deterministic local column suggestions; ambiguous workbooks require a choice."""
import re
from openpyxl.utils import get_column_letter
from app.excel.requirements import VALUE_HEADER, ANSWER_HEADER

INPUT_HEADER = re.compile(r'^(?:requirements?|technical\s+(?:requirements?|specifications?)|specifications?|description|原文|条款|技术要求|技术规格|需求描述)$', re.I)

def suggest(sheet):
    matches, candidates = [], []
    for row in sheet.iter_rows(max_row=min(sheet.max_row, 30)):
        for cell in row:
            header = str(cell.value or '').strip()
            if INPUT_HEADER.fullmatch(header) and not ANSWER_HEADER.search(header):
                samples = [str(sheet.cell(r, cell.column).value) for r in range(cell.row+1, min(sheet.max_row, cell.row+8)+1)
                           if sheet.cell(r, cell.column).value is not None and sheet.cell(r, cell.column).data_type != 'f']
                if any(re.search(r'[A-Za-z\u4e00-\u9fff]', v) for v in samples):
                    matches.append((cell.row, cell.column))
    if len(matches) == 1:
        header_row, input_col = matches[0]
        values = [get_column_letter(c.column) for c in sheet[header_row] if VALUE_HEADER.fullmatch(str(c.value or '').strip())]
        selected = {'sheet': sheet.title, 'header_row': header_row, 'input_column': get_column_letter(input_col),
                    'requirement_value_columns': values, 'output_mode': 'append', 'rows': list(range(header_row+1, sheet.max_row+1))}
    else:
        selected = None
        header_row = matches[0][0] if matches else 1
    for col in range(1, sheet.max_column+1):
        candidates.append({'column': get_column_letter(col), 'header': str(sheet.cell(header_row,col).value or ''),
                           'samples': [str(sheet.cell(r,col).value) for r in range(header_row+1,min(sheet.max_row,header_row+3)+1) if sheet.cell(r,col).value is not None]})
    return {'suggested_selection': selected, 'column_candidates': candidates,
            'needs_confirmation': [] if selected else ['header_row', 'input_column']}
