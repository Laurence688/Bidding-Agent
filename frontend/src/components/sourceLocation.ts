import type {Data} from '../api/client';
/** Original coordinates remain ordered when a logical section spans paragraphs and tables. */
export function sourceLocation(value:Data={}):string{
 const range=(first:any,last:any)=>`${first}${last&&last!==first?`–${last}`:''}`;
 const section=value.section_path?.length?value.section_path.join(' / '):value.section;
 if(value.section==='全文'&&!value.section_path?.length&&value.segments?.length)return sourceLocation({...value,segments:undefined});
 if(value.segments?.length)return [section,value.segments.map((part:Data)=>sourceLocation({...part,section_path:undefined,section:undefined,segments:undefined})).join('；')].filter(Boolean).join(' · ');
 const page=value.page_start??value.physical_page,paragraph=value.paragraph_start??value.paragraph;
 return [section,page?`第 ${range(page,value.page_end)} 页`:null,value.sheet?`工作表 ${value.sheet}`:null,value.table?`表 ${value.table}`:null,(value.row_start??value.row)?`第 ${range(value.row_start??value.row,value.row_end)} 行`:null,paragraph?`段落 ${range(paragraph,value.paragraph_end)}`:null,value.cells?`单元格 ${value.cells}`:null,value.part>1?`续片 ${value.part}`:null].filter(Boolean).join(' · ')||'原文定位待补充';
}
