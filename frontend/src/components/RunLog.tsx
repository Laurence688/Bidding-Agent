import {useEffect,useRef,useState} from 'react';
import {api,Data,downloadRunReport,label} from '../api/client';
import {ErrorNotice} from './Status';
import {duration,number,timestamp} from './ObservationCharts';

const logLabels:Record<string,string>={'job.status':'作业状态','job.progress':'节点完成','clause.committed':'条目已保存','job.error':'执行异常','review.approve':'批准结果','review.edit':'修改措辞','review.comment':'添加批注','review.revoke':'撤销审核','annotation.saved':'保存分析标注'};
export default function RunLog({runId}:{runId:string}){
 const [items,setItems]=useState<Data[]>([]),[cursor,setCursor]=useState<string|null>(null),[total,setTotal]=useState(0),[error,setError]=useState<any>(null),[busy,setBusy]=useState(false),[revision,setRevision]=useState(0),[filter,setFilter]=useState('all');
 const version=useRef(0);
 useEffect(()=>{const current=++version.current;setItems([]);setCursor(null);setTotal(0);setBusy(true);setError(null);api(`/runs/${runId}/logs`).then(data=>{if(current===version.current){setItems(data.items);setCursor(data.next_cursor);setTotal(data.total);}}).catch(e=>current===version.current&&setError(e)).finally(()=>current===version.current&&setBusy(false));return()=>{version.current++;};},[runId,revision]);
 async function more(){const current=version.current;setBusy(true);setError(null);try{const data=await api(`/runs/${runId}/logs?cursor=${encodeURIComponent(cursor!)}`);if(current===version.current){setItems(old=>[...old,...data.items]);setCursor(data.next_cursor);setTotal(data.total);}}catch(e){if(current===version.current)setError(e);}finally{if(current===version.current)setBusy(false);}}
 const visible=items.filter(row=>filter==='all'||row.kind===filter);
 return <section className="ob-run-log" aria-label="运行事件日志" aria-busy={busy}>
  <div className="section-bar"><div><h3>运行事件日志</h3><p className="muted">最近优先 · 已载入 {items.length} / {total} 条 · 刷新可查看新事件</p></div><div className="actions"><button onClick={()=>setRevision(v=>v+1)} disabled={busy}>刷新日志</button><button onClick={()=>downloadRunReport(runId).catch(setError)}>下载运行诊断</button></div></div>
  <ErrorNotice error={error}/><div className="filters" aria-label="日志类型">{[['all','全部'],['event','处理事件'],['model','模型调用'],['human','人工操作']].map(([value,title])=><button key={value} className={filter===value?'active':''} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{title}</button>)}</div>
  {!visible.length?<p className="empty compact">{busy?'正在读取日志…':items.length?'已加载记录中没有此类型，可继续加载更早记录。':'此运行还没有事件记录。'}</p>:<ol className="ob-timeline">{visible.map(row=><li key={row.id} className={row.type==='job.error'||row.status==='failed'?'ob-log-error':''}>
   <time>{row.timestamp_known===false?'历史调用时间未记录':timestamp(row.created_at)}</time><div><strong>{row.kind==='model'?`${label(row.logical_node_name)} · 模型调用`:logLabels[row.type]??row.type}</strong>
    <p>{row.kind==='model'?`${row.provider} / ${row.model} · 第 ${row.attempt_index} 次 · ${label(row.status)} · ${duration(row.latency)}`:row.status?label(row.status):row.current_node?label(row.current_node):row.processed_items!=null?`已处理 ${row.processed_items} 条`:''}</p>
    {row.kind==='model'&&<p className="muted">输入 {number(row.usage.input_tokens)} · 输出 {number(row.usage.output_tokens)} · 累计 {number(row.usage.total_tokens)} Token{row.phase==='review_validation'?' · 人工措辞校验':''}</p>}
    {(row.code||row.error_code)&&<p className="ob-log-code">{label(row.code??row.error_code)} <code>{row.code??row.error_code}</code></p>}
    {row.elapsed_seconds!=null&&<small>节点耗时 {duration(row.elapsed_seconds)}</small>}{row.clause_id&&<small className="mono">条目 {row.clause_id.slice(0,8)}</small>}
   </div></li>)}</ol>}
  {cursor&&<button className="ob-load-more" onClick={more} disabled={busy}>{busy?'正在读取…':'加载更早日志'}</button>}
 </section>;
}
