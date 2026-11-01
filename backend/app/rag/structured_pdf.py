import re
import pdfplumber
from pypdf import PdfReader
from app.domain.errors import DomainError
from app.rag.structured import clean, support, record, body_parts


def compact(text):
    return re.sub(r'\s+', '', text).casefold()


def parse_tables(path, document_type):
    result = []
    inherited = {'feature':['',''], 'performance':['','']}
    current = None
    with pdfplumber.open(path) as pdf:
        for pn,page in enumerate(pdf.pages,1):
            for ti,table in enumerate(page.find_tables(),1):
                for ri,values in enumerate(table.extract(x_tolerance=1),1):
                    values = [clean(v) for v in values]
                    kind = 'standard' if document_type == 'standard_list' else 'feature' if len(values)==5 else 'performance'
                    id_col = 0 if kind=='standard' else 2 if kind=='feature' else 1
                    if len(values) not in {4,5}: continue
                    number = values[id_col]
                    if not re.fullmatch(r'\d+',number):
                        # 页头和封面不是记录；只有明确空号且存在上一条时才追加续行。
                        if not number and current and current['content_type']==kind and any(values[id_col+1:]) and not any('category' in v.lower() or 'index' in v.lower() for v in values[:id_col+1]):
                            field = 'standard_content' if kind=='standard' else 'feature' if kind=='feature' else 'metric'
                            continuation = values[2] if kind=='standard' else values[id_col+1]
                            if continuation:
                                current['fields'][field] += ' '+continuation
                                current['locator']['page_end']=pn
                                current['quality_flags'].append('continued_row')
                        continue
                    if kind=='standard':
                        fields={'no':number,'category':None,'rfc':values[1],'standard_content':values[2],
                                'support_raw':values[3],'support':support(values[3])}
                        flags=['category_not_in_source']
                        section=[]
                    else:
                        context=inherited[kind]
                        for i in range(id_col):
                            if values[i]:context[i]=values[i]
                        fields={'category':context[0], 'line_no':number}
                        if kind=='feature':
                            fields.update(sub_category=context[1],feature=values[3],support_raw=values[4],support=support(values[4]))
                        else:
                            fields.update(metric=values[2],value=values[3])
                        flags=[]
                        section=['Feature Index' if kind=='feature' else 'Performance Index',context[0]]
                    current=record(kind,fields,{'kind':'pdf','page_start':pn,'page_end':pn,'table':ti,'row':ri},section,flags)
                    result.append(current)
    return result


def parse_description(path):
    reader=PdfReader(path)
    headings=[]
    def visit(items,level=1):
        for item in items:
            if isinstance(item,list):visit(item,level+1)
            else:headings.append({'title':clean(item.title),'page':reader.get_destination_page_number(item)+1,'level':level})
    visit(reader.outline)
    if not headings:
        raise DomainError('HEADINGS_REQUIRED','产品描述 PDF 缺少可核验标题书签，需要补充标题定位',422)
    result=[];stack=[];seen=set()
    with pdfplumber.open(path) as layout:
      for pn,page in enumerate(layout.pages,1):
        tables=page.find_tables()
        def outside_tables(obj):
            if obj.get('object_type')!='char':return True
            x=(obj['x0']+obj['x1'])/2;y=(obj['top']+obj['bottom'])/2
            return not any(t.bbox[0]<=x<=t.bbox[2] and t.bbox[1]<=y<=t.bbox[3] for t in tables)
        text_lines=page.filter(outside_tables).extract_text_lines(x_tolerance=1)
        events=[{'top':line['top'],'text':clean(line['text'])} for line in text_lines
                if clean(line['text']) and not clean(line['text']).isdigit() and compact(line['text'])!=compact(path.stem)]
        events.extend({'top':table.bbox[1],'table':table,'table_number':ti} for ti,table in enumerate(tables,1))
        events.sort(key=lambda event:event['top'])
        pending=[];part=0
        def flush():
            nonlocal part
            for text in body_parts(' '.join(pending)):
                part+=1
                result.append(record('description' if stack else 'front_matter',{'body':text},
                    {'kind':'pdf','page_start':pn,'page_end':pn,'part':part},stack.copy(),eligible=bool(stack)))
            pending.clear()
        i=0
        while i<len(events):
            event=events[i]
            if 'table' in event:
                flush()
                matrix=[[clean(cell) for cell in row] for row in event['table'].extract(x_tolerance=1)]
                if any(any(row) for row in matrix):
                    result.append(record('description_table',{'table_rows':matrix},
                        {'kind':'pdf','page_start':pn,'page_end':pn,'table':event['table_number']},stack.copy(),eligible=bool(stack)))
                i+=1
                continue
            matched=None
            for hi,h in enumerate(headings):
                if h['page']!=pn or hi in seen: continue
                for width in range(1,min(4,len(events)-i)+1):
                    selected=events[i:i+width]
                    if any('table' in item for item in selected):break
                    if compact(''.join(item['text'] for item in selected))==compact(h['title']):
                        matched=(hi,h,width); break
                if matched:break
            if matched:
                flush()
                hi,h,width=matched;seen.add(hi)
                stack[:]=stack[:h['level']-1]+[h['title']]
                i+=width
            else:
                pending.append(event['text']);i+=1
        flush()
    missing=[h['title'] for i,h in enumerate(headings) if i not in seen]
    if missing:
        raise DomainError('HEADINGS_UNMATCHED','PDF 书签无法匹配正文标题',422,{'headings':missing})
    return result


def parse_pdf(path, document_type):
    if document_type in {'feature_list','standard_list'}:
        return parse_tables(path,document_type)
    if document_type=='product_description':
        return parse_description(path)
    result=[]
    for pn,page in enumerate(PdfReader(path).pages,1):
        text=(page.extract_text() or '').strip()
        # 页面仍保留在 JSONL；封面、目录、结束页不进入语义证据索引。
        low=text.casefold()
        role='content'
        if pn==1: role='cover'
        elif re.search(r'thank\s*you|thanks|谢谢',low):role='closing'
        elif re.search(r'\b(agenda|contents)\b',low) and len(text.split())<90:role='agenda'
        elif len(text.split())<12:role='divider'
        result.append(record('roadmap_page',{'page_role':role,'body':text},
            {'kind':'pdf','page_start':pn,'page_end':pn},flags=['planning_only'],eligible=role=='content' and bool(text)))
    return result
