import {useState} from 'react';
import {api,Data,label} from '../api/client';
import {ErrorNotice} from './Status';

export const stages:Record<string,string>={input:'输入范围 / 非技术条款',translate:'翻译',split_claims:'要求拆分',retrieve:'检索',admit_evidence:'证据准入',judge_claim:'证据判定',verify_citations:'引用校验',verify_semantic_support:'语义核验',render_response:'英文应答',validate_output:'输出校验',model:'模型调用 / 截断',other:'其他'};
export const verdicts:Record<string,string>={correct:'正确',incorrect:'错误',uncertain:'待核实',not_requirement:'非技术条款'};

export function ClauseAnnotation({runId,row,stale,onSaved}:{runId:string;row:Data;stale:boolean;onSaved:(a:Data)=>void}){
 const [annotation,setAnnotation]=useState<Data|null>(row.annotation??null);
 const [verdict,setVerdict]=useState(row.annotation?.verdict??'uncertain');
 const [expected,setExpected]=useState(row.annotation?.expected_decision??'');
 const [stage,setStage]=useState(row.annotation?.stage??'');
 const [notes,setNotes]=useState(row.annotation?.notes??'');
 const [busy,setBusy]=useState(false),[error,setError]=useState<any>(null),[saved,setSaved]=useState(false);
 async function save(){setBusy(true);setError(null);setSaved(false);try{
  const updated=await api(`/runs/${runId}/clauses/${row.clause_id}/annotations`,'POST',{expected_version:annotation?.revision??0,result_id:row.result_id,verdict,expected_decision:expected||null,stage:stage||null,notes});
  setAnnotation(updated);setSaved(true);onSaved(updated);
 }catch(e){setError(e);}finally{setBusy(false);}}
 return <section className="annotation"><h3>分析标注</h3><p className="muted">保存人工评价，不改变自动结论或审核状态。每次保存保留历史，绑定当前结果。</p>
 {row.annotation_stale&&<p className="notice">已有标注针对旧结果，请重新核对。</p>}
 <div className="form-grid"><label>人工判断<select value={verdict} onChange={e=>{setVerdict(e.target.value);setSaved(false);}}>{Object.entries(verdicts).map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>
 <label>期望结论<select value={expected} onChange={e=>{setExpected(e.target.value);setSaved(false);}}><option value="">未指定</option>{['FC','PC','NC','UNKNOWN'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>问题环节<select value={stage} onChange={e=>{setStage(e.target.value);setSaved(false);}}><option value="">未定位</option>{Object.entries(stages).map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label></div>
 <label>分析备注<textarea rows={3} maxLength={4000} value={notes} onChange={e=>{setNotes(e.target.value);setSaved(false);}} placeholder="记录预期结果、缺失证据或可复现的问题"/></label>
 <ErrorNotice error={error}/><button onClick={save} disabled={busy||stale}>{busy?'正在保存标注…':'保存分析标注'}</button>{saved&&<p role="status">标注已保存 · 第 {annotation?.revision} 版</p>}
 </section>;
}

export function ClauseTrace({runId,clauseId}:{runId:string;clauseId:string}){
 const [trace,setTrace]=useState<Data|null>(null),[error,setError]=useState<any>(null),[busy,setBusy]=useState(false);
 async function load(){setBusy(true);setError(null);try{setTrace(await api(`/runs/${runId}/clauses/${clauseId}/trace`));}catch(e){setError(e);}finally{setBusy(false);}}
 return <section><h3>本条处理日志</h3><button onClick={load} disabled={busy}>{busy?'正在读取日志…':'查看节点与调用日志'}</button><ErrorNotice error={error}/>{trace&&<>
 <p className="muted">历史节点未记录时长时显示未知；模型调用耗时已单独记录。</p>
 {trace.steps.map((s:Data,i:number)=><details key={i}><summary>{label(s.node)} · {s.elapsed_seconds==null?'耗时未知':`${s.elapsed_seconds.toFixed(2)} 秒`}</summary><pre className="trace-json">{JSON.stringify(s.details,null,2)}</pre></details>)}
 <div className="table-scroll"><table><thead><tr><th>模型节点</th><th>状态 / 原因</th><th>秒</th><th>输入 / 输出 / 推理 Token</th></tr></thead><tbody>{trace.attempts.map((a:Data)=><tr key={a.attempt_id}><td>{label(a.logical_node_name)}<small>{a.is_retry?"网络重试":a.call_kind==="supplement"?"补检索追加":a.call_kind==="other_followup"?"其他后续调用":"首次调用"} · 预算尝试 {a.attempt_index} · {a.model}</small></td><td>{a.error_code??a.status}</td><td>{a.latency?.toFixed(2)??'未知'}</td><td>{a.usage?.input_tokens??'?'} / {a.usage?.output_tokens??'?'} / {a.usage?.reasoning_tokens??'?'}</td></tr>)}</tbody></table></div>
 </>}</section>;
}
