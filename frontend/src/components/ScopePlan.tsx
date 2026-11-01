import {useEffect,useState} from 'react';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';

export default function ScopePlan({runId,task,rows,sourceMetadata,onStarted}:{runId:string;task:Data;rows:Data[];sourceMetadata?:Data[];onStarted:()=>void}){
 const [plan,setPlan]=useState<Data|null>(null),[error,setError]=useState<any>(null),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false),[dirty,setDirty]=useState(false);
 const [runSources,setRunSources]=useState<Data[]|undefined>(undefined);
 const [date,setDate]=useState(''),[model,setModel]=useState(''),[source,setSource]=useState('');
 useEffect(()=>{let active=true;setPlan(null);setConfirmed(false);api(`/runs/${runId}/scope-plans`).then(value=>{
  if(!active)return;setRunSources(value.scope_sources);const plans=(value.items??[]).filter((p:Data)=>['draft','started'].includes(p.status));const latest=plans[plans.length-1];setPlan(latest??null);
  const life=latest?.lifecycle_context??value.lifecycle_context??{};
  setDate(life.reference_date??'');setModel(life.exact_model??'');setSource(life.source_version_id??'');setDirty(false);
 }).catch(e=>active&&setError(e));return()=>{active=false;};},[runId]);
 const sources=Array.from(new Map((sourceMetadata??runSources??rows.flatMap(r=>r.sources??[])).filter(s=>s.document_type==='roadmap').map(s=>[s.source_version_id,s])).values()) as Data[];
 async function prepare(){setBusy(true);setError(null);try{setPlan(await api(`/runs/${runId}/scope-plans`,'POST',{
  lifecycle_context:{reference_date:date||null,exact_model:model||null,source_version_id:source||null}
 }));setDirty(false);setConfirmed(false);}catch(e){setError(e);}finally{setBusy(false);}}
 async function start(){if(!plan||!confirmed||dirty)return;setBusy(true);setError(null);try{
  await api(`/scope-plans/${plan.id}/start`,'POST',{expected_version:task.version});setConfirmed(false);onStarted();
 }catch(e){setError(e);}finally{setBusy(false);}}
 return <section className="panel scope-plan" aria-label="修订应答范围"><h2>修订应答范围</h2>
 <p>按当前“不需应答”标注生成独立版本。未标注条目继续保留；历史结果与批注可继续对照。</p>
 <details><summary>生命周期核对条件（可选）</summary><p className="muted">仅当要求涉及 EOL / EOS 时使用。缺少日期、具体型号或资料版本时，Roadmap 仍不能作为已支持证据。</p>
 <div className="form-grid"><label>投标 / 核对参考日期<input type="date" value={date} onChange={e=>{setDate(e.target.value);setDirty(true);}}/></label>
 <label>具体设备型号<input value={model} placeholder="填写具体设备型号" onChange={e=>{setModel(e.target.value);setDirty(true);}}/></label>
 <label>生命周期资料版本<select value={source} onChange={e=>{setSource(e.target.value);setDirty(true);}}><option value="">未确认</option>{sources.map(s=><option key={s.source_version_id} value={s.source_version_id}>{s.source_name} · {s.version_label} · {s.source_version_id.slice(0,8)}</option>)}</select></label></div></details>
 <button onClick={prepare} disabled={busy}>{busy?'正在处理…':'生成范围方案'}</button><ErrorNotice error={error}/>
 {plan&&<><p className="scope-counts">原范围 <strong>{plan.original_count}</strong> 条 → 保留 <strong>{plan.included_count}</strong> 条；排除 <strong>{plan.excluded_count}</strong> 条</p>
 {plan.prior_excluded_count>0&&<p className="muted">另沿用此前已排除的 {plan.prior_excluded_count} 条及其标题上下文。</p>}
 <p className="muted">标题与同一行非输出列保留为理解上下文，新处理时提供给当前配置的模型；人工批注和输出列不发送。生成方案不会启动处理。</p>
 <details><summary>查看 {plan.excluded_count} 条排除项</summary>{plan.excluded.map((item:Data)=><p key={item.clause_id}><strong>{item.coordinate.sheet}!{item.coordinate.input_cell}</strong> · {item.raw_text}<br/><small>{item.notes}</small></p>)}</details>
 {plan.status==='draft'&&<><label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>确认按此范围启动 {plan.included_count} 条新处理，将调用当前配置的模型并产生费用</label>
 {dirty&&<p role="status">条件已修改，请重新生成方案。</p>}<button className="primary" onClick={start} disabled={busy||dirty||!confirmed}>启动此版本处理</button></>}
 {plan.status==='started'&&<p>此方案已启动。</p>}</>}
 </section>;
}
