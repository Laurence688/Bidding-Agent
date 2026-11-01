import re
from docx import Document
from docx.table import Table
from openpyxl import load_workbook
from app.domain.errors import DomainError
from app.rag.pagination import row_page, sheet_row_range
from app.rag.structured import clean, support, record, body_parts


def unique_cells(row):
    seen = set()
    for cell in row.cells:
        if cell._tc not in seen:
            seen.add(cell._tc)
            yield clean(cell.text)


def parse_excel(path, pagination):
    workbook = load_workbook(path, data_only=True)
    result = []
    try:
        for sheet in workbook:
            kind = 'performance' if 'performance' in sheet.title.casefold() else 'feature'
            id_col = 2 if kind == 'performance' else 3
            inherited = ['', '']
            groups = []
            current = None
            for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
                values = [clean(v) for v in values]
                if not any(values):
                    continue
                for col in range(id_col-1):
                    if values[col]:
                        inherited[col] = values[col]
                ident = values[id_col-1]
                if ident and not re.fullmatch(r'\d+', ident):
                    raise DomainError('STRUCTURE_INVALID', f'{sheet.title} 行 {number} 的编号无法识别', 422)
                if ident and (current is None or current['id'] != ident):
                    current = {'id': ident, 'category':inherited[0], 'sub_category':inherited[1], 'rows': [], 'texts': [], 'values': []}
                    groups.append(current)
                if current is None:
                    raise DomainError('STRUCTURE_INVALID', '表格续行没有可关联的编号', 422)
                current['rows'].append(number)
                if values[id_col]: current['texts'].append(values[id_col])
                if values[id_col+1]: current['values'].append(values[id_col+1])
            for group in groups:
                start, end = group['rows'][0], group['rows'][-1]
                fields = {'category':group['category'], 'line_no':group['id']}
                text = ' '.join(dict.fromkeys(group['texts']))
                value = '\n'.join(dict.fromkeys(group['values']))
                if kind == 'feature':
                    fields.update(sub_category=group['sub_category'], feature=text, support_raw=value, support=support(value))
                else:
                    fields.update(metric=text, value=value)
                result.append(record(kind, fields, {'kind':'xlsx', 'sheet':sheet.title, 'row':start, 'row_end':end,
                    'cells': f'A{start}:{"E" if kind=="feature" else "D"}{end}',
                    **sheet_row_range(pagination,sheet.title,start,end)},
                    [sheet.title, group['category']]))
    finally:
        workbook.close()
    return result


def parse_standard(path, pagination):
    result = []
    for ti, table in enumerate(Document(path).tables, 1):
        for ri, row in enumerate(table.rows, 1):
            values = list(unique_cells(row))
            if len(values) != 5 or not re.fullmatch(r'\d+', values[0]) or values[0] == '1':
                continue
            number, category, rfc, content, status = values
            flags = []
            if not rfc: flags.append('missing_rfc')
            if not content: flags.append('missing_standard_content')
            result.append(record('standard', {'no':number,'category':category or None,'rfc':rfc,
                'standard_content':content,'support_raw':status,'support':support(status)},
                {'kind':'docx','table':ti,'row':ri,**row_page(pagination,ti,ri)},[category] if category else [], flags))
    return result


def parse_description(path, pagination):
    result, stack, pending = [], [], []
    paragraphs = pagination.get('paragraphs', [])
    cursor, ti, pi = 0, 0, 0

    def flush():
        if not pending:
            return
        groups=[]
        for paragraph in pending:
            if not groups or (groups[-1][-1][1]['page_start'],groups[-1][-1][1]['page_end']) != (paragraph[1]['page_start'],paragraph[1]['page_end']):
                groups.append([])
            groups[-1].append(paragraph)
        for group in groups:
            for part_index, text in enumerate(body_parts(' '.join(p[0] for p in group))):
                loc = {'kind':'docx','paragraph':group[0][2], 'part':part_index+1,
                       'page_start':group[0][1]['page_start'], 'page_end':group[0][1]['page_end']}
                result.append(record('description', {'body':text}, loc, stack.copy(), eligible=bool(stack)))
        pending.clear()

    for item in Document(path).iter_inner_content():
        if isinstance(item, Table):
            flush(); ti += 1
            headers = list(unique_cells(item.rows[0]))
            for ri, row in enumerate(item.rows,1):
                values = list(unique_cells(row))
                if not any(values): continue
                result.append(record('table_row', {'table_headers':' | '.join(headers), 'row_content':' | '.join(values)},
                    {'kind':'docx','table':ti,'row':ri,**row_page(pagination,ti,ri)},stack.copy(), eligible=bool(stack)))
            continue
        pi += 1
        text = clean(item.text)
        if not text: continue
        # Word COM 在批注/域锚点末尾返回斜杠标记；正文仍采用 OOXML 原文。
        match = next((j for j in range(cursor,len(paragraphs)) if clean(paragraphs[j]['text']).rstrip('/') == text.rstrip('/')), None)
        if match is None:
            raise DomainError('PAGINATION_UNMATCHED', f'段落 {pi} 无法匹配 Word 页码：{text[:60]}', 422)
        loc = paragraphs[match]; cursor = match+1
        heading = re.match(r'(?:Heading|标题)\s*(\d+)', item.style.name, re.I)
        if heading:
            flush()
            level = int(heading[1])
            stack[:] = stack[:level-1] + [text]
        else:
            pending.append((text,loc,pi))
    flush()
    return result


def parse_office(path, document_type, pagination):
    if path.suffix.lower() == '.xlsx':
        return parse_excel(path,pagination)
    if document_type == 'standard_list':
        return parse_standard(path,pagination)
    if document_type == 'product_description':
        return parse_description(path,pagination)
    raise DomainError('FORMAT_UNSUPPORTED','没有配置该 Office 文档的结构化解析规则',422)
