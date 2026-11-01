import {useEffect,useRef,useState} from 'react';
import {api,Data} from '../api/client';
import {Badge,ErrorNotice} from './Status';
import {modeName,timestamp} from './ObservationCharts';
import './decision-insights.css';

const states=['FC','PC','NC','UNKNOWN'] as const;
const names:Record<string,string>={FC:'完全符合',PC:'部分符合',NC:'不符合',UNKNOWN:'待补证 / 未决'};
const colors:Record<string,string>={FC:'#167954',PC:'#b47b20',NC:'#bf5550',UNKNOWN:'#7c6bb0'};
const percent=(value:number|null|undefined)=>value==null?'—':`${(value*100).toFixed(1)}%`;
type Choice={decision:string;reason:string;title:string};
type Props={projectId:string;sessionId?:string;overview:Data;onTask:(id:string)=>void};

export default function DecisionInsights({projectId,sessionId,overview,onTask}:Props){
 const [taskId,setTaskId]=useState(''),[choice,setChoice]=useState<Choice|null>(null);
 const [items,setItems]=useState<Data[]>([]),[cursor,setCursor]=useState<string|null>(null),[total,setTotal]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState<any>(null),[refresh,setRefresh]=useState(0);
 const generation=useRef(0),morePending=useRef(false);
 const tasks=overview.tasks.filter((task:Data)=>task.mode!=='PRODUCT_QA');
 const task=tasks.find((item:Data)=>item.id===taskId),activeTask=task?.id??'';
 const outcomes:Data|undefined=task?.outcomes??overview.summary.outcomes;
 // Live count updates must not discard pages or expanded explanations being read.
 const runRevision=JSON.stringify((task?[task]:tasks).map((t:Data)=>[t.id,t.active_run_id]));
 const params=new URLSearchParams();
 if(sessionId)params.set('session_id',sessionId);
 if(activeTask)params.set('task_id',activeTask);
 if(choice?.decision)params.set('decision',choice.decision);
 if(choice?.reason)params.set('reason',choice.reason);
 const path=`/projects/${projectId}/observability/clauses?${params.toString()}`;

 useEffect(()=>{
  const current=++generation.current;setItems([]);setCursor(null);setTotal(0);setError(null);morePending.current=false;
  if(!choice){setBusy(false);return;}
  setBusy(true);
  api(path).then(data=>{if(current===generation.current){setItems(data.items);setCursor(data.next_cursor);setTotal(data.total);}})
   .catch(e=>current===generation.current&&setError(e)).finally(()=>current===generation.current&&setBusy(false));
  return()=>{generation.current++;};
 },[path,!!choice,runRevision,refresh]);

 async function more(){
  if(!cursor||busy||morePending.current)return;
  const current=generation.current;morePending.current=true;setBusy(true);setError(null);
  try{const result=await api(`${path}&cursor=${encodeURIComponent(cursor)}`);if(current===generation.current){setItems(old=>[...old,...result.items]);setCursor(result.next_cursor);setTotal(result.total);}}
  catch(e){if(current===generation.current)setError(e);}
  finally{if(current===generation.current){morePending.current=false;setBusy(false);}}
 }

 if(!outcomes)return null;
 const reasons:Data[]=outcomes.reasons??[];
 let offset=0;
 return <section className="panel decision-insights" aria-label="Laura处理结果">
  <div className="outcome-heading">
   <div><p className="outcome-eyebrow">结论与原因</p><h2>Laura处理结果</h2><p className="muted">当前运行的机器原始结论 · 不受人工措辞修改影响</p></div>
   <label>结果统计范围<select aria-label="结果统计范围" value={activeTask} onChange={e=>{setTaskId(e.target.value);setChoice(null);}}><option value="">本范围内的全部 SOC 任务</option>{tasks.map((t:Data)=><option key={t.id} value={t.id}>{modeName(t.mode)} · {timestamp(t.created_at)} · {t.id.slice(0,8)}</option>)}</select></label>
  </div>
  <div className="outcome-layout">
   <div className="outcome-distribution">
    <div className="outcome-ring-wrap">
     <svg viewBox="0 0 180 180" className="outcome-ring" role="img" aria-label={`Agent 结论分布：${states.map(s=>`${s} ${outcomes.counts[s]} 条`).join('，')}`}>
      <title>按已完成的 {outcomes.processed} 条 SOC 结果计算占比，未完成不计入 UNKNOWN。</title>
      <circle cx="90" cy="90" r="72" fill="none" stroke="#edf1f7" strokeWidth="15"/>
      {states.map(state=>{const share=outcomes.processed?outcomes.counts[state]/outcomes.processed*100:0;const start=offset;offset+=share;return share>0?<circle key={state} cx="90" cy="90" r="72" pathLength="100" fill="none" stroke={colors[state]} strokeWidth="15" strokeDasharray={`${share} ${100-share}`} strokeDashoffset={-start} transform="rotate(-90 90 90)"/>:null;})}
      <text x="90" y="88" textAnchor="middle" className="outcome-ring-number">{outcomes.processed.toLocaleString('zh-CN')}</text>
      <text x="90" y="111" textAnchor="middle" className="outcome-ring-label">已完成 SOC 条目</text>
     </svg>
     <div className="outcome-completion"><span>共 {outcomes.total} 条</span><span className={outcomes.pending?'has-pending':''}>未完成 {outcomes.pending} 条</span></div>
    </div>
    <div className="outcome-cards">{states.map(state=><button key={state} aria-label={`查看 ${state} 条目`} aria-pressed={choice?.decision===state&&!choice.reason} className={`outcome-card outcome-${state.toLowerCase()}`} onClick={()=>setChoice({decision:state,reason:'',title:`${state} · ${names[state]}`})}>
     <span className="outcome-card-title"><i style={{background:colors[state]}}/>{state}<small>{names[state]}</small></span>
     <span className="outcome-card-value"><strong>{outcomes.counts[state].toLocaleString('zh-CN')}</strong><small>条</small><b>{percent(outcomes.rates[state])}</b></span>
    </button>)}</div>
    <p className="outcome-scope-note">占比以已完成 SOC 为分母；产品问答不计入。UNKNOWN 为未决，不等于 NC。</p>
   </div>

   <div className="outcome-reasons">
    <div className="outcome-reasons-heading"><div><h3>非 FC 原因分布 <span>{outcomes.non_fc} 条</span></h3><p>点击分类，查看对应条目与机器记录的原因</p></div><button className="outcome-show-all" onClick={()=>setChoice({decision:'NON_FC',reason:'',title:'全部非 FC 条目'})} disabled={!outcomes.non_fc}>查看全部非 FC <span aria-hidden>↗</span></button></div>
    <div className="outcome-reason-legend">{states.filter(s=>s!=='FC').map(state=><span key={state}><i style={{background:colors[state]}}/>{state}</span>)}</div>
    {!outcomes.processed?<div className="outcome-reason-empty">{outcomes.pending?'等待 Agent 生成 SOC 结果':'当前范围尚无 SOC 结果'}<small>生成后显示结论数量和原因分布。</small></div>:!outcomes.non_fc?<div className="outcome-reason-empty all-fc">已完成的 SOC 条目全部为 FC<small>目前没有需要分类的非 FC 结果。</small></div>:<div className="outcome-reason-list">{reasons.map(reason=><button className={`outcome-reason${choice?.reason===reason.key?' selected':''}`} key={reason.key} aria-label={`查看原因：${reason.label}`} aria-pressed={choice?.reason===reason.key} onClick={()=>setChoice({decision:'NON_FC',reason:reason.key,title:reason.label})}>
     <span className="outcome-reason-top"><strong>{reason.label}</strong><span>{reason.count} 条 <b>{percent(reason.rate)}</b></span></span>
     <span className="outcome-reason-track" aria-hidden>{states.filter(s=>s!=='FC').map(state=><span key={state} style={{width:`${outcomes.non_fc?(reason.decisions[state]??0)/outcomes.non_fc*100:0}%`,background:colors[state]}}/>)}</span>
     <span className="outcome-reason-bottom">{states.filter(s=>s!=='FC'&&reason.decisions[s]>0).map(s=>`${s} ${reason.decisions[s]}`).join(' · ')}<span aria-hidden>查看条目 →</span></span>
    </button>)}</div>}
    <p className="outcome-reason-note">同一条目可能有多个原因，各类别分别去重；占比以 {outcomes.non_fc} 条非 FC 为分母，类别合计可超过 100%。</p>
   </div>
  </div>

  {choice&&<section className="outcome-detail" aria-label="结果条目明细" aria-busy={busy}>
   <div className="section-bar"><div><h3>{choice.title}</h3><p className="muted">匹配 {total} 条 · 已加载 {items.length} 条 · Agent 原始结果</p></div><div className="actions"><button onClick={()=>setRefresh(v=>v+1)} disabled={busy}>刷新条目</button><button onClick={()=>setChoice(null)}>收起条目</button></div></div>
   <p className="muted">图表更新会保留已加载条目；点击“刷新条目”查看最新匹配结果。</p><ErrorNotice error={error}/>
   {!items.length?<p className="empty compact">{busy?'正在读取对应条目…':error?'暂时无法读取条目，请刷新重试。':'当前范围没有匹配的条目。'}</p>:<div className="outcome-clause-list">{items.map(item=><article className="outcome-clause" key={item.id}>
    <div className="outcome-clause-head"><div><Badge value={item.decision}/><span className="mono">{item.coordinate?.sheet?`${item.coordinate.sheet}!${item.coordinate.input_cell??item.coordinate.row}`:'文本条款'} · 任务 {item.task_id.slice(0,8)}</span></div><button onClick={()=>onTask(item.task_id)}>打开对应任务</button></div>
    <p className="outcome-raw-text">{item.raw_text}</p>
    <div className="outcome-tags">{item.reason_categories.map((category:Data)=><span key={category.key}>{category.label}</span>)}</div>
    {item.gaps.length>0&&<details className="outcome-gaps"><summary>查看 {item.gaps.length} 个未满足或未决要求</summary>{item.gaps.map((gap:Data,i:number)=><div key={i}><strong>{gap.verdict==='CONTRADICTED'?'证据表明不满足':'尚未得到确认'}</strong><p>{gap.text||'未记录具体要求文本'}</p>{gap.explanation&&<p className="muted">机器解释：{gap.explanation}</p>}</div>)}</details>}
    {item.reason_codes.length>0&&<p className="outcome-codes"><span>记录的原因码</span>{item.reason_codes.map((code:string)=><code key={code}>{code}</code>)}</p>}
   </article>)}</div>}
   {cursor&&<button className="ob-load-more" disabled={busy} onClick={more}>{busy?'正在读取…':'加载更多条目'}</button>}
  </section>}
 </section>;
}
