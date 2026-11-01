from pathlib import Path
from docx import Document
from docx.table import Table
from pypdf import PdfReader
from app.domain.errors import DomainError

PARSER_VERSION = 'structured-v1'


def parse_document(path):
    path = Path(path)
    try:
        if path.suffix.lower() == '.docx':
            return _docx(path)
        if path.suffix.lower() == '.pdf':
            return _pdf(path)
        raise DomainError('SOURCE_FORMAT', '知识来源格式未授权', 415)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError('SOURCE_PARSE_FAILED', '来源解析失败，原件已保留', 422, {'type': type(exc).__name__}) from exc


def _docx(path):
    document = Document(path)
    blocks, section, paragraph, table_index = [], '正文', 0, 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            table_index += 1
            for row_index, row in enumerate(item.rows, 1):
                # 合并单元格可能重复 XML 节点，只提取一次，不造出重复证据。
                cells, seen = [], set()
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        cells.append(cell.text.strip())
                text = ' | '.join(cells)
                if text.strip(' |'):
                    blocks.append({'text': text, 'locator': {'kind': 'docx', 'section': section, 'table': table_index, 'row': row_index}})
        else:
            paragraph += 1
            text = item.text.strip()
            if not text:
                continue
            style = item.style.name if item.style else ''
            if style.startswith(('Heading', '标题')):
                section = text
            blocks.append({'text': text, 'locator': {'kind': 'docx', 'section': section, 'paragraph': paragraph}})
    if not blocks:
        raise DomainError('SOURCE_EMPTY', '文档没有可提取正文', 422)
    return blocks


def _pdf(path):
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise DomainError('SOURCE_ENCRYPTED', '不支持加密知识文件', 415)
    blocks = []
    for page_index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or '').strip()
        # 空白封面可跳过；有图像但无文本的页不可假装已解析。
        if not text:
            if list(page.images):
                raise DomainError('SOURCE_OCR_REQUIRED', '来源含无法提取文本的图像页，需人工处理', 422, {'physical_page': page_index})
            continue
        blocks.append({'text': text, 'locator': {'kind': 'pdf', 'physical_page': page_index, 'printed_page': None}})
    if not blocks:
        raise DomainError('SOURCE_EMPTY', 'PDF 无可提取文本', 422)
    return blocks
