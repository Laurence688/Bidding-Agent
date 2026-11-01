import re
import posixpath
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
from openpyxl.utils.cell import column_index_from_string, get_column_letter, coordinate_from_string, range_boundaries
from app.domain.errors import DomainError

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def sheet_paths(package):
    relations = ET.fromstring(package.read('xl/_rels/workbook.xml.rels'))
    targets = {r.attrib['Id']: (r.attrib['Target'].lstrip('/') if r.attrib['Target'].startswith('/') else posixpath.normpath('xl/' + r.attrib['Target'])) for r in relations}
    book = ET.fromstring(package.read('xl/workbook.xml'))
    return {sheet.attrib['name']: targets[sheet.attrib[f'{{{REL}}}id']] for sheet in book.findall(f'{{{NS}}}sheets/{{{NS}}}sheet')}


def patch_sheet(raw, values):
    text = raw.decode('utf-8')
    tree = ET.fromstring(raw)
    originals = {c.attrib['r']: c for c in tree.iter(f'{{{NS}}}c')}
    for coordinate, value in sorted(values.items(), key=lambda pair: (coordinate_from_string(pair[0])[1], column_index_from_string(coordinate_from_string(pair[0])[0]))):
        column, row = coordinate_from_string(coordinate)
        if len(str(value)) > 32767 or any(ord(c) < 32 and c not in '\t\n\r' for c in str(value)):
            raise DomainError('CELL_VALUE_INVALID', '回填内容超过单元格限制或含控制字符', 422, {'cell': coordinate})
        attrs = dict(originals[coordinate].attrib) if coordinate in originals else {'r': coordinate}
        attrs['t'] = 'inlineStr'
        row_pattern = re.compile(r'<(?P<prefix>(?:\w+:)?)row\b[^>]*\br=[\"\']' + str(row) + r'[\"\'][^>]*>(?P<body>.*?)</(?:\w+:)?row>', re.S)
        row_match = row_pattern.search(text)
        if row_match is None:
            raise DomainError('TARGET_ROW_MISSING', '输出行不在已确认原表范围', 422)
        prefix = row_match.group('prefix')
        cell = '<' + prefix + 'c ' + ' '.join(f'{k}={quoteattr(str(v))}' for k, v in attrs.items()) + '><' + prefix + 'is><' + prefix + 't xml:space="preserve">' + escape(str(value)) + '</' + prefix + 't></' + prefix + 'is></' + prefix + 'c>'
        body = row_match.group('body')
        cell_pattern = re.compile(r'<(?:\w+:)?c\b[^>]*\br=[\"\']' + re.escape(coordinate) + r'[\"\'][^>]*(?:/>|>.*?</(?:\w+:)?c>)', re.S)
        if cell_pattern.search(body):
            new_body = cell_pattern.sub(lambda _: cell, body, count=1)
        else:
            insert_at = len(body)
            for match in re.finditer(r'<(?:\w+:)?c\b[^>]*\br=[\"\']([A-Z]+)\d+[\"\']', body):
                if column_index_from_string(match.group(1)) > column_index_from_string(column):
                    insert_at = match.start()
                    break
            new_body = body[:insert_at] + cell + body[insert_at:]
        text = text[:row_match.start('body')] + new_body + text[row_match.end('body'):]
    dimension = re.search(r'<(?:\w+:)?dimension\b[^>]*\bref="([^"]+)"[^>]*/>', text)
    if dimension:
        min_col, min_row, max_col, max_row = range_boundaries(dimension.group(1))
        for coordinate in values:
            col, row = coordinate_from_string(coordinate)
            min_col, min_row = min(min_col, column_index_from_string(col)), min(min_row, row)
            max_col, max_row = max(max_col, column_index_from_string(col)), max(max_row, row)
        updated = f'{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}'
        text = text[:dimension.start(1)] + updated + text[dimension.end(1):]
    return text.encode('utf-8')


def patch_workbook(source, output, values):
    if Path(source).resolve() == Path(output).resolve():
        raise DomainError('SOURCE_OVERWRITE_DENIED', '不能覆盖输入文件', 409)
    with ZipFile(source) as original, ZipFile(output, 'w') as target:
        names = sheet_paths(original)
        if not set(values).issubset(names):
            raise DomainError('SHEET_NOT_FOUND', '输出工作表不存在', 422)
        parts = {names[name]: cells for name, cells in values.items()}
        target.comment = original.comment
        for info in original.infolist():
            raw = original.read(info.filename)
            target.writestr(info, patch_sheet(raw, parts[info.filename]) if info.filename in parts else raw)
