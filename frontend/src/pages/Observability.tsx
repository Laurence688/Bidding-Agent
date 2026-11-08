import {Collapse} from 'antd';
import {useEffect,useRef,useState} from 'react';
import {api,Data,label} from '../api/client';
import {Badge,ErrorNotice} from '../components/Status';
import {duration,metricText,modeName,NodeChart,number,QualityCards,stateColors,stateOrder,timestamp,tokens,UsageTrend} from '../components/ObservationCharts';
import RunLog from '../components/RunLog';
import DecisionInsights from '../components/DecisionInsights';
import './observability.css';

type Props={project:Data|null;onTask:(id:string,mode?:string,runId?:string)=>void;initialTaskId?:string;initialRunId?:string};
export default function Observability({project,onTask,initialTaskId,initialRunId}:Props){
 const [data,setData]=useState<Data|null>(null),[sessions,setSessions]=useState<Data[]>([]),[scope,setScope]=useState(''),[error,setError]=useState<any>(null),[busy,setBusy]=useState(false),[auto,setAuto]=useState(true),[revision,setRevision]=useState(0);
 const [tab,setTab]=useState('tasks'),[status,setStatus]=useState(''),[search,setSearch]=useState(''),[chart,setChart]=useState<'tokens'|'time'>('tokens'),[selected,setSelected]=useState(initialTaskId??''),[runId,setRunId]=useState(initialRunId??'');
 const runDetail=useRef<HTMLElement>(null);
 useEffect(()=>{
  setSelected(initialTaskId??'');
  setRunId(initialRunId??'');
  if(initialRunId)setScope('');
 },[initialTaskId,initialRunId]);
 useEffect(()=>{if(!project)return;let active=true,timer:ReturnType<typeof setTimeout>;setData(null);
  async function refresh(){setBusy(true);try{const value=await api(`/projects/${project!.id}/observability${scope?`?session_id=${encodeURIComponent(scope)}`:''}`);if(active){setData(value);setSessions(value.sessions);setError(null);}}catch(e){if(active)setError(e);}finally{if(active){setBusy(false);if(auto)timer=setTimeout(refresh,5000);}}}
  refresh();return()=>{active=false;clearTimeout(timer);};
 },[project?.id,scope,auto,revision]);
 const summary=data?.summary,usage=summary?.usage,task=data?.tasks.find((t:Data)=>t.id===selected);
 const chosenRun=runId?task?.runs.find((r:Data)=>r.id===runId)?.id:task?.active_run_id;
 const linkedRunReady=Boolean(initialRunId&&chosenRun===initialRunId);
 useEffect(()=>{
  if(!linkedRunReady)return;
  const frame=requestAnimationFrame(()=>runDetail.current?.scrollIntoView?.({block:'start',behavior:'instant'}));
  return()=>cancelAnimationFrame(frame);
 },[initialRunId,linkedRunReady]);
 if(!project)return <div className="empty">先在工作台创建项目，观测页会按项目展示任务与会话。</div>;
 const filtered=data?.tasks.filter((t:Data)=>(!status||t.status===status)&&(!search||`${t.id} ${t.session_name} ${modeName(t.mode)} ${t.product_id}`.toLowerCase().includes(search.toLowerCase())))??[];
 function chooseScope(value:string){setScope(value);setSelected('');setRunId('');}
 return <div className="observability">
  <div className="page-heading"><div><p className="eyebrow">本地运行记录 / {project.name}</p><h1>观测</h1><p>从会话到模型调用，查看处理进度、资源消耗与人工反馈。</p></div><button onClick={()=>setRevision(v=>v+1)} disabled={busy}>刷新观测</button></div>
  <div className="ob-toolbar"><label>会话范围<select value={scope} onChange={e=>chooseScope(e.target.value)}><option value="">全部会话与未关联任务</option>{sessions.map(s=><option key={s.id} value={s.id}>{project.name} - {s.name}{s.deleted?'（已删除）':''}</option>)}</select></label><label className="check"><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/>每 5 秒自动刷新</label><span className="muted" role="status">{busy?'正在更新…':data?`更新于 ${timestamp(data.generated_at)}（北京时间）`:'等待数据'}</span></div>
  <ErrorNotice error={error}/>{error&&data&&<p className="notice">刷新失败，当前展示上次成功取得的数据。</p>}
  {data&&selected&&runId&&!chosenRun&&<p className="notice" role="status">当前项目中找不到所选运行。</p>}
  {!data||!summary||!usage?<div className="empty">{error?'暂时无法读取观测数据，请点击刷新观测重试。':'正在汇总任务与会话…'}</div>:<>
   <Collapse defaultActiveKey={['overview']} items={[{key:'overview',forceRender:true,label:<span>运行概况 · {summary.task_count} 个任务 · 异常 {(summary.statuses.failed??0)+(summary.statuses.interrupted??0)+(summary.statuses.partial??0)}</span>,children:<>   <section className="panel ob-overview" aria-label="运行概况"><div className="ob-stat-grid">
    <div><span>任务数</span><strong data-testid="task-count">{summary.task_count}</strong><small>共 {summary.run_count} 次运行，含重跑</small></div>
    <div><span>当前处理进度</span><strong>{summary.processed_items}<em> / {summary.total_items}</em></strong><small>当前运行的条目数，含产品问答</small></div>
    <div><span>累计 Token</span><strong className="ob-token-number">{tokens(usage)}</strong><small>{usage.attempt_count} 次模型请求 · {usage.missing_attempt_count??0} 次用量缺失</small></div>
    <div><span>模型累计耗时</span><strong>{duration(usage.model_latency_seconds??usage.known_model_latency_seconds)}</strong><small>{usage.model_latency_seconds==null&&usage.attempt_count>0?'已记录耗时小计':'包括失败、重试和人工措辞校验'}</small></div>
   </div><div className="ob-state-strip" role="img" aria-label={`任务状态：${Object.entries(summary.statuses).map(([s,n])=>`${label(s)} ${n}`).join('，')||'暂无任务'}`}>{stateOrder.filter(s=>summary.statuses[s]).map(s=><span key={s} title={`${label(s)} ${summary.statuses[s]}`} style={{width:`${summary.statuses[s]/summary.task_count*100}%`,background:stateColors[s]}}/>)}</div><div className="ob-state-legend">{stateOrder.map(s=><button key={s} aria-pressed={status===s} onClick={()=>{setStatus(status===s?'':s);setTab('tasks');}}><i style={{background:stateColors[s]}}/>{s==='unknown'?'未记录':label(s)} <b>{summary.statuses[s]??0}</b></button>)}</div>
   <p className="muted">输入 {number(usage.known_input_tokens)} · 输出 {number(usage.known_output_tokens)} · 缓存命中 {number(usage.known_cached_tokens)} Token（已知小计；缓存与推理不重复累加）。</p></section></>} ]}/>
   <Collapse defaultActiveKey={['decisions']} items={[{key:'decisions',forceRender:true,label:'Laura处理结果 · SOC 四态与未决',children:<DecisionInsights key={`${project.id}:${scope}`} projectId={project.id} sessionId={scope} overview={data} onTask={onTask}/>} ]}/>
   <Collapse items={[{key:'quality',forceRender:true,label:<span>人工质量与审核 · 有效标注 {summary.quality.annotated} 条</span>,children:<QualityCards quality={summary.quality}/>} ]}/>
   <details className="ob-definitions"><summary>统计口径与样本范围</summary><p>采纳率按当前结果的批准状态计算，不等于事实准确或已经交付。人工介入只计修改、批注、撤销审核，每条计一次；普通批准和分析标注不计入。</p><p>准确率只反映当前结果中已明确标注正确／错误的样本，不代表整体模型准确率。有效标注 {summary.quality.annotated} 条；不确定或非需求 {summary.quality.excluded_annotations} 条不进入准确率分母；另有 {summary.quality.stale_annotations} 条过期标注已排除。</p><p>三项业务比率仅统计当前运行的 SOC 条目；产品问答、拆解和翻译不参与。Token 与模型耗时累计所有运行和人工措辞校验，缺失值显示未知。任务历时从首次启动开始，包含中断后的恢复等待，不含首次排队。</p></details>
   <div className="ob-charts"><section className="panel"><div className="section-bar"><div><h2>近 14 天资源趋势</h2><p className="muted">调用开始日期 · 北京时间</p></div><div className="filters">{[['tokens','Token'],['time','模型耗时']].map(([value,title])=><button key={value} className={chart===value?'active':''} aria-pressed={chart===value} onClick={()=>setChart(value as 'tokens'|'time')}>{title}</button>)}</div></div><UsageTrend days={data.trend} view={chart}/>{data.undated_attempt_count>0&&<p className="muted">{data.undated_attempt_count} 次历史调用未记录开始时间，已计入累计，未放入日期趋势。</p>}</section><section className="panel"><h2>模型节点耗时</h2><p className="muted">所有运行 · 已记录的请求耗时</p><NodeChart nodes={data.nodes}/></section></div>
   <section className="panel ob-list" aria-label="任务与会话明细"><div className="section-bar"><div className="tabs"><button className={tab==='tasks'?'active':''} aria-pressed={tab==='tasks'} onClick={()=>setTab('tasks')}>任务明细</button><button className={tab==='sessions'?'active':''} aria-pressed={tab==='sessions'} onClick={()=>setTab('sessions')}>会话明细</button></div>{tab==='tasks'&&<div className="ob-list-filters"><label><span className="sr-only">任务状态</span><select aria-label="任务状态" value={status} onChange={e=>setStatus(e.target.value)}><option value="">全部状态</option>{stateOrder.map(s=><option key={s} value={s}>{label(s)}</option>)}</select></label><label><span className="sr-only">搜索任务</span><input aria-label="搜索任务" placeholder="任务 ID、会话或型号" value={search} onChange={e=>setSearch(e.target.value)}/></label></div>}</div>
    {tab==='tasks'?(!filtered.length?<div className="empty compact">{data.tasks.length?'没有符合筛选条件的任务。':'当前范围还没有任务，提交任务后会显示运行记录。'}</div>:<div className="table-scroll"><table className="ob-task-table"><thead><tr><th>任务 / 会话</th><th>处理情况</th><th>排队 / 任务历时</th><th>Token / 调用</th><th>采纳 / 准确 / 介入</th><th>操作</th></tr></thead><tbody>{filtered.map((t:Data)=><tr key={t.id} className={selected===t.id?'row-selected':''}><td><strong>{modeName(t.mode)}</strong> <span className="product-pill">{t.product_id}</span><small>{t.display_name??`${project?.name} - ${t.session_name}`}</small><small>{timestamp(t.created_at)} · {t.id.slice(0,8)}</small></td><td><Badge value={t.status}/><small>{t.processed_items} / {t.total_items} 条 · {t.current_node?label(t.current_node):'尚无节点记录'}</small><progress max={Math.max(1,t.total_items)} value={t.processed_items} aria-label={`任务 ${t.id.slice(0,8)} 处理进度`}/>{t.error_code&&<small className="ob-log-code">{label(t.error_code)}</small>}</td><td>{duration(t.queue_seconds)}<small>历时 {duration(t.wall_seconds)}{t.status==='running'?'（进行中）':''}</small></td><td>{tokens(t.usage)}<small>{t.usage.attempt_count} 次调用 · {t.runs.length} 次运行</small></td><td>{!['SOC_SINGLE','SOC_BATCH'].includes(t.mode)?'不参与 SOC 指标':<>采纳 {metricText(t.quality.adoption)}<small>准确 {metricText(t.quality.accuracy)}</small><small>介入 {metricText(t.quality.intervention)}</small></>}</td><td><div className="ob-row-actions"><button onClick={()=>{setSelected(t.id);setRunId(t.active_run_id);}}>查看日志</button><button onClick={()=>onTask(t.id,t.mode,t.active_run_id)}>打开任务</button></div></td></tr>)}</tbody></table></div>):<div className="table-scroll"><table><thead><tr><th>会话</th><th>任务 / 处理</th><th>Token / 模型耗时</th><th>采纳 / 标注正确 / 介入</th><th>操作</th></tr></thead><tbody>{data.sessions.filter((s:Data)=>!scope||s.id===scope).map((s:Data)=><tr key={s.id}><td><strong>{project?.name} - {s.name}{s.deleted?"（已删除）":""}</strong>{s.deleted&&<small>会话已删除，任务记录保留</small>}<small>{s.created_at?timestamp(s.created_at):'历史未关联任务'}</small></td><td>{s.task_count} 个任务 · {s.run_count} 次运行<small>{s.processed_items} / {s.total_items} 条已处理</small><small>{Object.entries(s.statuses??{}).map(([k,v])=>`${label(k)} ${v}`).join(' · ')||'尚无任务'}</small></td><td>{tokens(s.usage)}<small>{duration(s.usage.model_latency_seconds??s.usage.known_model_latency_seconds)}</small></td><td>{[['adoption','采纳'],['accuracy','准确'],['intervention','介入']].map(([key,title])=><small key={key}>{title} {metricText(s.quality[key])}</small>)}</td><td><button onClick={()=>{chooseScope(s.id);setTab('tasks');}}>查看会话任务</button></td></tr>)}</tbody></table>{!data.sessions.length&&<p className="muted">项目尚无会话。</p>}</div>}
   </section>
   {task&&chosenRun&&<section ref={runDetail} className="panel ob-detail" aria-label="所选任务日志"><div className="section-bar"><div><h2>{modeName(task.mode)} · {task.id.slice(0,8)}</h2><p className="muted">{task.display_name??`${project?.name} - ${task.session_name}`} · {task.product_id}</p></div><button onClick={()=>{setSelected('');setRunId('');}}>关闭日志</button></div><label>日志运行版本<select value={chosenRun} onChange={e=>setRunId(e.target.value)}>{task.runs.map((r:Data)=><option key={r.id} value={r.id}>{r.active?'当前运行':'历史运行'} · {timestamp(r.created_at)} · {label(r.status)} · {r.id.slice(0,8)}</option>)}</select></label><RunLog key={chosenRun} runId={chosenRun}/></section>}
  </>}
 </div>;
}
