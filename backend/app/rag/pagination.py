import json
import shutil
import uuid
from pathlib import Path
from app.domain.errors import DomainError
from app.domain.identifiers import file_hash, content_hash
from app.security.local_access import controlled_path


def load_pagination(path, rendered_root):
    """Office 页码只来自与原件哈希绑定的固定 PDF；不估算 Word/Excel 页码。"""
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        return {}
    digest = file_hash(path)
    folder = Path(rendered_root) / digest
    manifest = folder / 'pagination.json'
    if not manifest.is_file():
        raise DomainError('PAGINATION_REQUIRED', '缺少固定 PDF 页码，请先运行 scripts/render_knowledge.ps1', 422)
    data = json.loads(manifest.read_text('utf-8-sig'))
    pdf = controlled_path(folder, data['pdf_name'])
    if data['source_sha256'] != digest or not pdf.is_file() or file_hash(pdf) != data['pdf_sha256']:
        raise DomainError('PAGINATION_CHANGED', '原件或固定 PDF 校验失败，不能复用页码', 422)
    from pypdf import PdfReader
    if len(PdfReader(pdf).pages) != data['page_count']:
        raise DomainError('PAGINATION_INVALID', '固定 PDF 页数与页码映射不一致', 422)
    return data


def publish_pagination(manifest_path, rendered_root):
    """发布不可变版式；latest 指针可变，但旧引用绑定的 PDF 和映射永不覆盖。"""
    manifest_path, rendered_root = Path(manifest_path), Path(rendered_root)
    data = json.loads(manifest_path.read_text('utf-8-sig'))
    pdf = controlled_path(manifest_path.parent,data['pdf_name'])
    if not pdf.is_file() or file_hash(pdf)!=data['pdf_sha256']:
        raise DomainError('PAGINATION_CHANGED','固定 PDF 校验失败，不能发布',422)
    from pypdf import PdfReader
    if len(PdfReader(pdf).pages)!=data['page_count']:
        raise DomainError('PAGINATION_INVALID','固定 PDF 页数与页码映射不一致',422)
    identity={k:v for k,v in data.items() if k!='layout_fingerprint'}
    identity['pdf_name']=pdf.name
    layout=content_hash(identity)
    folder=controlled_path(rendered_root,f"{data['source_sha256']}/{layout}")
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/pdf.name
    if target.exists() and file_hash(target)!=data['pdf_sha256']:
        raise DomainError('PAGINATION_IMMUTABLE','既有固定 PDF 版本内容不一致',409)
    if not target.exists():shutil.copyfile(pdf,target)
    published={**data,'pdf_name':f'{layout}/{pdf.name}','layout_fingerprint':layout}
    frozen=folder/'pagination.json'
    if frozen.exists() and json.loads(frozen.read_text('utf-8'))!=published:
        raise DomainError('PAGINATION_IMMUTABLE','既有定位版本不允许覆盖',409)
    if not frozen.exists():frozen.write_text(json.dumps(published,ensure_ascii=False,indent=2),'utf-8')
    latest=folder.parent/'pagination.json'
    temporary=folder.parent/f'.pagination-{uuid.uuid4().hex}.json'
    temporary.write_text(json.dumps(published,ensure_ascii=False,indent=2),'utf-8')
    temporary.replace(latest)
    return published


def pagination_fingerprint(data):
    return content_hash(data)


def row_page(pagination, table, row):
    try:
        return pagination['tables'][str(table)][str(row)].copy()
    except KeyError:
        raise DomainError('PAGINATION_REQUIRED', f'表 {table} 行 {row} 缺少真实页码', 422)


def sheet_page(pagination, sheet, row):
    try:
        data = pagination['sheets'][sheet]
        if 'row_pages' in data:
            return data['row_pages'][str(row)]['page_start']
        breaks = data['row_breaks']
        for index, (start, end) in enumerate(zip(breaks, breaks[1:]), 1):
            if start <= row < end:
                return data['page_offset'] + index
    except KeyError:
        pass
    raise DomainError('PAGINATION_REQUIRED', f'{sheet} 行 {row} 缺少打印页码', 422)


def sheet_row_range(pagination,sheet,start,end):
    data=pagination['sheets'][sheet]
    if 'row_pages' in data:
        return {'page_start':data['row_pages'][str(start)]['page_start'], 'page_end':data['row_pages'][str(end)]['page_end']}
    return {'page_start':sheet_page(pagination,sheet,start),'page_end':sheet_page(pagination,sheet,end)}


def validate_number_pages(expected, actual, page_count):
    if set(expected)!=set(actual):
        raise DomainError('PAGINATION_NUMBER_MISMATCH','打印 PDF 编号覆盖不完整',422,
                          {'missing':sorted(set(expected)-set(actual)),'unexpected':sorted(set(actual)-set(expected))})
    if any(not 1<=loc['page_start']<=loc['page_end']<=page_count for loc in actual.values()):
        raise DomainError('PAGINATION_INVALID','打印 PDF 页码超出真实页数',422)
