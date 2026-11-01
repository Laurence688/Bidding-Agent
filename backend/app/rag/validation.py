"""预处理、入库、建索引和激活共用的结构完整性门。"""
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash


def records_digest(records):
    return content_hash(sorted([{'id':r.get('chunk_id',r.get('id')),'text':r['text'],'fields':r.get('fields',{}),
        'locator':r['locator'],'eligible':r.get('retrieval_eligible',True),'source_order':r.get('source_order')} for r in records],key=lambda r:r['id']))


def validate_records(records, expected_numbers=None, page_count=None, document_type=None):
    if not records:
        raise DomainError('SOURCE_EMPTY','来源没有有效记录',422)
    ids=[r.get('chunk_id',r.get('id')) for r in records]
    if len(set(ids))!=len(ids):
        raise DomainError('STRUCTURE_DUPLICATE','结构化记录 ID 重复',422)
    for row in records:
        loc=row['locator'];start=loc.get('page_start',0);end=loc.get('page_end',0)
        if not 1<=start<=end or (page_count is not None and end>page_count):
            raise DomainError('PAGINATION_INVALID','记录页码超出原件实际范围',422)
    for kind,bounds in (expected_numbers or {}).items():
        try:
            actual=sorted(int(r['fields'].get('line_no',r['fields'].get('no'))) for r in records if r['content_type']==kind)
        except (ValueError,TypeError,KeyError):
            raise DomainError('STRUCTURE_NUMBER_INVALID','结构化记录编号无效',422)
        expected=list(range(bounds[0],bounds[1]+1))
        if actual!=expected:
            raise DomainError('STRUCTURE_COVERAGE_FAILED','结构化记录编号不完整或重复，保留旧活动快照',422,
                {'content_type':kind,'expected_count':len(expected),'actual_count':len(actual),
                 'missing':sorted(set(expected)-set(actual)),'unexpected':sorted(set(actual)-set(expected))})
    if document_type=='roadmap' and page_count is not None:
        pages=sorted(r['locator']['page_start'] for r in records)
        if pages!=list(range(1,page_count+1)):
            raise DomainError('STRUCTURE_COVERAGE_FAILED','Roadmap 页面覆盖不完整',422)
    return {'record_count':len(records),'records_digest':records_digest(records),'expected_numbers':expected_numbers or {},
            'page_count':page_count,'document_type':document_type}
