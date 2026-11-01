"""类型化知识记录。content 是可核验原文与字段，embedding_text 不含文件/页码噪声。"""
import re
import uuid
from pathlib import Path
from app.domain.identifiers import file_hash, content_hash, canonical
from app.domain.errors import DomainError
from app.rag.pagination import pagination_fingerprint

SCHEMA_VERSION = 'structured-v2.2'


def clean(value):
    return re.sub(r'\s+', ' ', str('' if value is None else value).replace('\x07', '').replace('\x00', '').replace('\x1e','-')).strip()


def support(value):
    value = clean(value)
    if value.casefold() in {'support', 'supported', 'yes', 'y', '√', '✓', '✔'}:
        return 'Support'
    if value.casefold() in {'not support', 'unsupported', 'no', 'n', '×', '✗'}:
        return 'Not support'
    return value or None


def body_parts(text, limit=300, overlap=35):
    """正文按空白词边界分段，长原子表格记录不走这里；向量层另做 token 窗口。"""
    words = text.split()
    start = 0
    while start < len(words):
        end = min(start + limit, len(words))
        yield ' '.join(words[start:end])
        if end == len(words):
            break
        start = end - overlap


def record(kind, fields, locator, section_path=None, flags=None, eligible=True):
    return {'content_type': kind, 'fields': fields, 'locator': locator,
            'section_path': section_path or [], 'quality_flags': flags or [], 'retrieval_eligible': eligible}


def parse_structured(path, product_id, document_type, pagination=None):
    path = Path(path)
    pagination = pagination or {}
    if path.suffix.lower() in {'.docx', '.xlsx'} and not pagination:
        raise DomainError('PAGINATION_REQUIRED', 'Office 文档需要固定 PDF 的真实页码', 422)
    if path.suffix.lower() == '.pdf':
        from app.rag.structured_pdf import parse_pdf
        rows = parse_pdf(path, document_type)
    else:
        from app.rag.structured_office import parse_office
        rows = parse_office(path, document_type, pagination)
    digest = file_hash(path)
    fingerprint = content_hash({'parser': SCHEMA_VERSION, 'pagination': pagination_fingerprint(pagination)})
    finalized = []
    for ordinal, row in enumerate(rows):
        loc = row['locator']
        if not loc.get('page_start') or loc.get('page_end', 0) < loc['page_start']:
            raise DomainError('PAGINATION_INVALID', '结构化片段缺少有效页码', 422)
        loc['physical_page'] = loc['page_start']
        loc['section_path'] = row['section_path']
        loc['section'] = ' > '.join(row['section_path']) or None
        loc['page_source'] = ('native_pdf' if path.suffix.lower() == '.pdf' else 'excel_print_pdf' if path.suffix.lower() == '.xlsx' else 'word_rendered_pdf')
        if pagination.get('pdf_name'):
            loc['rendered_pdf'] = f"{digest}/{pagination['pdf_name']}"
        labels = {'sub_category':'Sub-category', 'line_no':'Line No.', 'no':'No.', 'standard_content':'Content', 'support_raw':'Original support', 'table_headers':'Table headers'}
        lines = []
        for key,value in row['fields'].items():
            if value is None or value == '':continue
            if key=='table_rows':value='\n'+'\n'.join(' | '.join(cells) for cells in value)
            lines.append(f'{labels.get(key, key.replace("_", " ").capitalize())}: {value}')
        semantic = '\n'.join([f'Product: {product_id}', ' > '.join(row['section_path']), *lines]).strip()
        page_label = str(loc['page_start']) if loc['page_start'] == loc['page_end'] else f"{loc['page_start']}-{loc['page_end']}"
        content = f'Source: {path.name}\nPage: {page_label}\n' + semantic
        identity = {'source': digest, 'product': product_id, 'type': document_type, 'parser': fingerprint, 'ordinal': ordinal, 'record': row}
        finalized.append({**row, 'schema_version': SCHEMA_VERSION, 'chunk_id': str(uuid.uuid5(uuid.NAMESPACE_URL, canonical(identity))),
            'source_order': ordinal,
            'source_sha256': digest, 'source_file': path.name, 'product_id': product_id, 'document_type': document_type,
            'parser_fingerprint': fingerprint, 'content': content, 'text': content, 'embedding_text': semantic,
            'content_hash': content_hash(content)})
    return finalized
