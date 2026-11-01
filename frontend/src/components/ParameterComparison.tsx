import {Data} from '../api/client';

export default function ParameterComparison({row}:{row:Data}){
 const required=row.source_context?.requirement_values??row.result?.diagnostics?.requirement_values??[];
 const facts=(row.result?.assessments??[]).flatMap((a:Data)=>(a.parameter_values??[]).map((v:Data)=>{
  const ref=a.evidence_refs.find((r:Data)=>r.chunk_id===v.evidence_chunk_id);
  const check=row.result.checks?.find((c:Data)=>c.ref_id===ref?.id);
  return {...v,ref,verdict:a.verdict,explanation:a.explanation,verified:check?.semantic_support==='SUPPORTED'};
 }));
 if(!required.length&&!facts.length)return null;
 return <section className="parameter-comparison"><h3>参数要求与产品实际值</h3>
 <p className="muted">要求值来自原表；实际值来自产品资料，须结合型号、配置和适用条件核对。</p>
 {required.map((v:Data)=><p key={v.cell}>要求 {v.cell}：{v.comparator==='unspecified'?'比较方向待核对':v.comparator} {v.raw_value} {v.unit}</p>)}
 {facts.length?<div className="table-scroll"><table><thead><tr><th>参数</th><th>产品实际值</th><th>适用条件</th><th>来源与核验</th></tr></thead><tbody>{facts.map((v:Data,i:number)=><tr key={i}>
 <td>{v.name}</td><td>{v.actual_value}{v.unit&&!v.actual_value.endsWith(v.unit)?` ${v.unit}`:''}</td><td>{v.conditions||'见引用原文'}</td>
 <td>{v.ref?.source_name??'来源缺失'} · {v.ref?.version_label}<small>{[v.ref?.locator?.page_start?`p.${v.ref.locator.page_start}`:v.ref?.locator?.physical_page?`p.${v.ref.locator.physical_page}`:null,v.ref?.locator?.sheet,v.ref?.locator?.cells,v.ref?.locator?.table?`table ${v.ref.locator.table}`:null,v.ref?.locator?.row?`row ${v.ref.locator.row}`:null].filter(Boolean).join('!')}</small>
 <small>{v.verified&&v.verdict!=='UNKNOWN'?'已核验子项值':'待核验提取值'} · {v.explanation}</small></td></tr>)}</tbody></table></div>:<p className="notice">本结果尚无经过来源绑定的产品实际值；历史结果不会自动改写。</p>}
 </section>;
}
