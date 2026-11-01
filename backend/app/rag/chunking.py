def chunk_blocks(blocks, max_chars=1800):
    """优先保持段落/表格条目；长页沿换行切分并保留原结构定位。"""
    for block in blocks:
        if block.get('content_type') in {'feature', 'performance', 'standard', 'roadmap_page', 'table_row', 'description_table'}:
            yield block
            continue
        text = block['text']
        offset = 0
        while offset < len(text):
            end = min(offset + max_chars, len(text))
            if end < len(text):
                boundary = text.rfind('\n', offset + max_chars // 2, end)
                if boundary > offset:
                    end = boundary + 1
            part = text[offset:end].strip()
            if part:
                yield {**block, 'text': part, 'block_offset': offset}
            offset = end


# Versioned wizard strategies are separate from legacy chunk_blocks semantics.
SPLIT_PARSER_VERSION = 'wizard-sections-v1'
WHOLE_DOCUMENT_PARSER_VERSION = 'wizard-whole-document-v1'

def _heading_level(paragraph):
    from docx.oxml.ns import qn
    import re
    style=paragraph.style
    if style is not None and re.match(r'(?:TOC|目录)\s*\d*',style.name or '',re.I):return None
    candidates=[paragraph._p.pPr];names=[];seen=set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id);candidates.append(style.element.pPr);names.append(style.name or '');style=style.base_style
    for properties in candidates:
        if properties is not None:
            outline=properties.find(qn('w:outlineLvl'))
            if outline is not None:
                level=int(outline.get(qn('w:val')))
                return level+1 if level<9 else None
    for name in names:
        match=re.match(r'(?:Heading|标题)\s*([1-9])',name,re.I)
        if match:return int(match.group(1))
    return None

def docx_sections(path, whole_document=False):
    """Keep each branch's leaf section intact, retaining ordered source spans."""
    from docx import Document
    from docx.table import Table
    from docx.oxml.ns import qn
    document = Document(path)
    comments = {}
    try:
        comments = {str(c.comment_id): c.text for c in document.comments}
    except (AttributeError, KeyError):
        pass
    result, stack, pending, paragraph, table = [], [], [], 0, 0
    heading, has_heading = None, False
    def flush(child=False):
        nonlocal pending, heading
        # A pure parent heading contributes only to descendants' section_path.
        if pending or (heading and not child):
            entries = ([heading] if heading else []) + pending
            loc = {'kind':'docx','section_path':[v[1] for v in stack], 'segments':[e[1] for e in entries]}
            loc['section'] = '全文' if whole_document else stack[-1][1] if stack else '前言'
            paragraphs=[e[1]['paragraph_start'] for e in entries if e[1]['kind']=='paragraph']
            if paragraphs:loc.update(paragraph=paragraphs[0],paragraph_start=min(paragraphs),paragraph_end=max(paragraphs))
            result.append({'text':'\n'.join(e[0] for e in entries),'locator':loc})
        pending, heading = [], None
    def comment_entries(item,locator):
        for anchor in item._p.iter(qn('w:commentRangeStart')):
            ident=anchor.get(qn('w:id'));comment=comments.get(ident)
            if comment:pending.append((comment,{'kind':'comment',**locator,'comment_id':ident}))
    def walk_table(value):
        nonlocal table
        table+=1;table_id=table
        for n,row in enumerate(value.rows,1):
            seen=set();parts=[]
            locator={'table':table_id,'row_start':n,'row_end':n}
            def row_flush():
                if parts:
                    pending.append((' | '.join(parts),{'kind':'table_row',**locator}));parts.clear()
            for cell in row.cells:
                if cell._tc in seen:continue
                seen.add(cell._tc)
                for child in cell.iter_inner_content():
                    if isinstance(child,Table):
                        row_flush();walk_table(child)
                    else:
                        text=child.text.strip()
                        if text:parts.append(text)
                        if list(child._p.iter(qn('w:commentRangeStart'))):
                            row_flush();comment_entries(child,locator)
            row_flush()
    for item in document.iter_inner_content():
        if isinstance(item,Table):walk_table(item)
        else:
            paragraph+=1
            text=item.text.strip()
            level=_heading_level(item) if text else None
            if level and not whole_document:
                has_heading=True
                flush(child=bool(stack and level>stack[-1][0]))
                while stack and stack[-1][0]>=level:stack.pop()
                stack.append((level,text));heading=(text,{'kind':'paragraph','paragraph_start':paragraph,'paragraph_end':paragraph})
            elif text:pending.append((text,{'kind':'paragraph','paragraph_start':paragraph,'paragraph_end':paragraph}))
            comment_entries(item,{'paragraph_start':paragraph,'paragraph_end':paragraph})
    flush()
    return result,([] if has_heading or whole_document else ['未识别到标题，请改选段落合并策略并重新预览'])

def split_blocks(blocks,config):
    strategy=config.get('strategy','default');size=config.get('max_chars',1000);overlap=config.get('overlap',0)
    chunks=[]
    def add(text,loc):
        if text.strip():chunks.append({'text':text,'locator':loc,'source_order':len(chunks)})
    def pieces(text,loc):
        offset=0
        while offset<len(text):
            add(text[offset:offset+size],{**loc,'part':offset//max(1,size-overlap)+1})
            if offset+size>=len(text):break
            offset+=size-overlap
    if strategy=='whole_document':
        if blocks:
            loc={'kind':blocks[0]['locator']['kind'],'section':'全文','segments':[
                dict(segment) for block in blocks for segment in block['locator'].get('segments',[block['locator']])]}
            paragraphs=[segment.get('paragraph_start') or segment.get('paragraph') for segment in loc['segments']]
            paragraphs=[number for number in paragraphs if number is not None]
            endings=[segment.get('paragraph_end') or segment.get('paragraph_start') or segment.get('paragraph') for segment in loc['segments']]
            if paragraphs:loc.update(paragraph_start=min(paragraphs),paragraph_end=max(number for number in endings if number is not None))
            pages=[segment.get('physical_page') or segment.get('page_start') for segment in loc['segments']]
            pages=[number for number in pages if number is not None]
            if pages:loc.update(page_start=min(pages),page_end=max(segment.get('page_end') or segment.get('physical_page') or segment.get('page_start') for segment in loc['segments'] if segment.get('physical_page') or segment.get('page_start')))
            add('\n'.join(block['text'] for block in blocks),loc)
    elif strategy in {'leaf_sections','excel_records'}:
        for b in blocks:add(b['text'],b['locator'])
    elif strategy in {'paragraphs','fixed','delimiter'}:
        # Merge source spans as well as text, preserving their original order.
        def merged(values):
            loc=dict(values[0]['locator']);loc['segments']=[dict(b['locator']) for b in values]
            return '\n'.join(b['text'] for b in values),loc
        if strategy=='paragraphs':
            # One ordered source stream makes overlap apply across group boundaries,
            # including the suffix of the preceding paragraph/table row.
            text='\n'.join(block['text'] for block in blocks)
            spans=[];cursor=0
            for block in blocks:
                end=cursor+len(block['text'])
                spans.append((cursor,end,block['locator']))
                cursor=end+1
            boundaries=[start for start,_,_ in spans[1:]]
            start=0
            while start<len(text):
                end=min(start+size,len(text))
                if end<len(text):
                    candidates=[boundary for boundary in boundaries if start+overlap<boundary<=end]
                    if candidates:end=candidates[-1]
                source_spans=[dict(locator) for left,right,locator in spans if left<end and right>start]
                if source_spans:
                    loc={**source_spans[0],'segments':source_spans,'part':len(chunks)+1}
                    add(text[start:end],loc)
                if end==len(text):break
                start=end-overlap
        else:
            text,loc=merged(blocks) if blocks else ('',{})
            if strategy=='delimiter':
                delimiter=config['delimiter'];parts=text.split(delimiter)
                for index,value in enumerate(parts):pieces(value+(delimiter if index<len(parts)-1 else ''),loc)
            else:pieces(text,loc)
    else:
        for b in blocks:pieces(b['text'],b['locator'])
    return chunks
