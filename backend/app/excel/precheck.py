from pathlib import Path
import posixpath
import re
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from openpyxl import load_workbook
from app.excel.suggestions import suggest

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
CAPABILITY_VERSION = 'ooxml-whitelist-v2'
ALLOWED_PART = re.compile(
    r'^(\[Content_Types\]\.xml|_rels/\.rels|docProps/(app|core|custom)\.xml|xl/('
    r'workbook\.xml|styles\.xml|sharedStrings\.xml|calcChain\.xml|_rels/workbook\.xml\.rels|'
    r'theme/theme\d+\.xml|worksheets/sheet\d+\.xml|worksheets/_rels/sheet\d+\.xml\.rels|'
    r'drawings/vmlDrawing\d+\.vml|drawings/_rels/vmlDrawing\d+\.vml\.rels))$')
ALLOWED_IMAGE = re.compile(r'^xl/media/image\d+\.(jpeg|jpg|png)$')
VML = 'urn:schemas-microsoft-com:vml'
OFFICE = 'urn:schemas-microsoft-com:office:office'
STATIC_VML_TAGS = {'xml'} | {f'{{{VML}}}{tag}' for tag in (
    'shapetype', 'shape', 'path', 'fill', 'stroke', 'imagedata', 'formulas', 'f')}
STATIC_VML_TAGS |= {f'{{{OFFICE}}}{tag}' for tag in ('shapelayout', 'idmap', 'lock')}
IMAGE_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'


def static_vml(root):
    # VML also carries form controls and OLE objects; accept only passive pictures.
    shapes = list(root.iter(f'{{{VML}}}shape'))
    return (root.tag == 'xml' and bool(shapes)
        and all(element.tag in STATIC_VML_TAGS for element in root.iter())
        and not any(key.rsplit('}', 1)[-1].lower() in {'src', 'href'}
                    for element in root.iter() for key in element.attrib)
        and all(shape.attrib.get('type') == '#_x0000_t75'
                and shape.find(f'{{{VML}}}imagedata') is not None for shape in shapes))


def precheck(path):
    path = Path(path)
    issues, sheets = [], []
    if path.suffix.lower() != '.xlsx' or path.stat().st_size > MAX_FILE_BYTES:
        return {'accepted': False, 'issues': [{'code': 'FORMAT_OR_SIZE', 'location': 'file'}], 'sheets': []}
    try:
        with ZipFile(path) as package:
            infos = package.infolist()
            if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES or len({i.filename for i in infos}) != len(infos):
                return {'accepted': False, 'issues': [{'code': 'ZIP_LIMIT', 'location': 'package'}], 'sheets': []}
            for info in infos:
                if info.flag_bits & 1:
                    issues.append({'code': 'ENCRYPTED_PART', 'location': info.filename})
                    continue
                # Some producers include explicit ZIP directories; these are not OOXML parts.
                if info.is_dir() and info.file_size == 0:
                    continue
                image = ALLOWED_IMAGE.fullmatch(info.filename)
                if not image and not ALLOWED_PART.fullmatch(info.filename):
                    issues.append({'code': 'UNSUPPORTED_PART', 'location': info.filename})
                    continue
                raw = package.read(info.filename)
                if image:
                    signature = b'\x89PNG\r\n\x1a\n' if image.group(1) == 'png' else b'\xff\xd8\xff'
                    if not raw.startswith(signature):
                        issues.append({'code': 'UNSUPPORTED_IMAGE', 'location': info.filename})
                    # Images are preserved as bytes by package_patch, never parsed as XML.
                    continue
                if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                    issues.append({'code': 'UNSUPPORTED_XML', 'location': info.filename})
                    continue
                root = ET.fromstring(raw)
                if info.filename.endswith('.vml') and not static_vml(root):
                    issues.append({'code': 'UNSUPPORTED_VML', 'location': info.filename})
                for element in root.iter():
                    if element.attrib.get('TargetMode') == 'External' or 'macroEnabled' in str(element.attrib):
                        issues.append({'code': 'EXTERNAL_OR_MACRO', 'location': info.filename})
                if info.filename.endswith('.vml.rels'):
                    for relation in root:
                        target = relation.attrib.get('Target', '')
                        target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/drawings/' + target)
                        if relation.attrib.get('Type') != IMAGE_REL or not ALLOWED_IMAGE.fullmatch(target) or target not in package.namelist():
                            issues.append({'code': 'UNSUPPORTED_VML', 'location': info.filename})
            if issues:
                return {'accepted': False, 'issues': issues, 'sheets': []}
        book = load_workbook(path, data_only=False, keep_links=False)
        for sheet in book.worksheets:
            if sheet.max_row * sheet.max_column > 200000:
                issues.append({'code': 'SHEET_SIZE_LIMIT', 'location': sheet.title})
                continue
            formulas = [c.coordinate for row in sheet for c in row if c.data_type == 'f']
            preview = [[{'cell': c.coordinate, 'value': str(c.value) if c.value is not None else ''} for c in row] for row in sheet.iter_rows(max_row=min(sheet.max_row, 12), max_col=min(sheet.max_column, 20))]
            sheets.append({'name': sheet.title, 'max_row': sheet.max_row, 'max_column': sheet.max_column, 'formulas': formulas,
                'merged_ranges': [str(r) for r in sheet.merged_cells.ranges], 'hidden_rows': [n for n, d in sheet.row_dimensions.items() if d.hidden],
                'hidden_columns': [n for n, d in sheet.column_dimensions.items() if d.hidden], 'preview': preview, **suggest(sheet)})
        book.close()
        return {'accepted': not issues, 'issues': issues, 'sheets': sheets, 'capability_version': CAPABILITY_VERSION}
    except (BadZipFile, OSError, KeyError, ValueError, TypeError, ET.ParseError) as exc:
        return {'accepted': False, 'issues': [{'code': 'INVALID_XLSX', 'location': 'package', 'type': type(exc).__name__}], 'sheets': []}
