import {useEffect,useState,useRef} from 'react';
import {Button} from 'antd';
import {CheckOutlined,LoadingOutlined,BorderOutlined,SyncOutlined,RightOutlined} from '@ant-design/icons';
import {api,Data,label,streamJob} from '../api/client';
export default function RunActivity({runId,job,onRefresh,active=true}:{runId:string;job?:Data;onRefresh:()=>void|Promise<void>;active?:boolean}){
 const [value,setValue]=useState<Data|null>(null),[now,setNow]=useState(Date.now()),[stopping,setStopping]=useState(false),[error,setError]=useState('');
 const [expanded,setExpanded]=useState(false);
 useEffect(()=>{setExpanded(false);},[runId,job?.status]);
 const refresh=useRef(onRefresh);refresh.current=onRefresh;
 useEffect(()=>{if(!active)return;let live=true,inFlight=false,terminal=false;let controller:AbortController|undefined;let retry:ReturnType<typeof setTimeout>|undefined;
  setValue(null);setError('');setStopping(false);
  async function load(){if(inFlight)return null;inFlight=true;try{const data=await api(`/runs/${runId}/activity`);if(live){setValue(data);const ended=Boolean(data.job)&&!['queued','running'].includes(data.job.status);if(ended&&!terminal){await refresh.current();if(live)setExpanded(false);}terminal=ended;}return data;}catch{return null;}finally{inFlight=false;}}
  async function connect(){const snapshot=await load();if(!live)return;if(!snapshot?.job||!['running','queued'].includes(snapshot.job.status))return;
   controller=new AbortController();try{await streamJob(snapshot.job.id,snapshot.cursor,controller.signal,()=>{void load();void Promise.resolve(refresh.current()).catch(()=>{});});}catch{ /* authoritative snapshot + polling remain available */ }
   if(live)retry=setTimeout(connect,1500);
  }
  void connect();const poll=setInterval(()=>{if(!terminal)void load();},1800),clock=setInterval(()=>setNow(Date.now()),500);
  return()=>{live=false;controller?.abort();clearInterval(poll);clearInterval(clock);if(retry)clearTimeout(retry);};
 },[runId,active,job?.status]);
 const current=value?.job??job,status=current?.status,running=['running','queued'].includes(status);
 async function action(name:string){if(!current)return;setError('');if(name==='cancel')setStopping(true);try{if(current.kind==='document_task')await api(`/document-tasks/runs/${runId}/${name}`,'POST',{});else await api(`/jobs/${current.id}/${name}`,'POST',{expected_version:current.version});refresh.current();const next=await api(`/runs/${runId}/activity`);setValue(next);}catch(e){setError((e as Error).message);setStopping(false);}}
 const usage=value?.usage??{},count=(v:unknown)=>typeof v==='number'?v.toLocaleString():'—';
 return <div className="laura-activity">
  {!running&&<button className="run-summary-toggle" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>用时 {value?.elapsed_seconds==null?'—':`${Math.floor(value.elapsed_seconds/60)}分${Math.floor(value.elapsed_seconds%60)}秒`} <RightOutlined rotate={expanded?90:0}/></button>}
  {(running||expanded)&&<>{running&&<div className="activity-heading"><LoadingOutlined/><span>{status==='queued'?'等待处理':'思考中'}</span><Button type="text" size="small" aria-label="停止任务" title="停止任务" disabled={stopping} icon={<BorderOutlined/>} onClick={()=>void action('cancel')}/>{stopping&&<small>正在停止</small>}</div>}
   <ol className="activity-steps">{(value?.steps??[]).map((step:Data)=><li key={step.id}><span>{step.status==='running'?<LoadingOutlined/>:step.status==='completed'||step.status==='reused'?<CheckOutlined/>:'○'}</span><span>{label(step.node)===step.node?'处理任务':label(step.node)}{step.status==='reused'?' · 已复用':step.status==='failed'?' · 未完成':''}</span><small>{step.status==='running'?Math.max(0,now/1000-step.started_at).toFixed(1):(step.elapsed_seconds==null?'—':step.elapsed_seconds.toFixed(1))}s</small></li>)}</ol>
   {current?.total_items>1&&<small>已核验 {current.processed_items??0} / {current.total_items} 条</small>}</>}
  {!running&&status&&status!=='completed'&&<p>{label(status)}{current?.error?.message&&` · ${current.error.message}`}{(status==='interrupted'||(current?.kind==='document_task'&&status==='cancelled'))&&<Button type="text" aria-label="恢复任务" icon={<SyncOutlined/>} onClick={()=>void action('resume')}>恢复任务</Button>}{current?.kind==='document_task'&&['failed','partial'].includes(status)&&<Button type="text" onClick={()=>void action('retry')}>重试失败部分</Button>}{current?.kind==='document_task'&&['failed','partial','cancelled','interrupted'].includes(status)&&<Button type="text" onClick={()=>void action('rerun')}>重新运行</Button>}</p>}
  {!running&&expanded&&<div className="response-usage" title="从首次开始至最终结束，包含恢复间隔；不包含后续人工审核调用。">{usage.partial?'已知用量 · ':''}输入 {count(usage.input_tokens)} · 输出 {count(usage.output_tokens)} Token · 总耗时 {value?.elapsed_seconds==null?'—':`${value.elapsed_seconds.toFixed(1)}s`}</div>}
  {error&&<small role="alert">{error}</small>}
 </div>;
}
