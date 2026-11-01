import {useEffect,useState,useRef} from 'react';
import {Button,Popover,Tooltip} from 'antd';
import {CompressOutlined} from '@ant-design/icons';
import {api,Data,label} from '../api/client';

type Props={mode?:string;runId?:string;active?:boolean;sessionId?:string;productId?:string;rawText?:string;referenceMessageId?:string;referenceClauseId?:string;targetLanguage?:string;busy?:boolean;onNewSession:()=>void};
const finiteBudget=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const count=(value:unknown)=>finiteBudget(value)?value.toLocaleString():'未知';
export default function ContextUsage({sessionId,productId,rawText='',referenceMessageId,referenceClauseId,targetLanguage='zh',busy=false,onNewSession,mode='PRODUCT_QA',runId,active=true}:Props){
 const scope=`${sessionId??''}:${productId??''}`;const currentScope=useRef(scope);currentScope.current=scope;
 const [requestChanged,setRequestChanged]=useState(true);
 useEffect(()=>{if(rawText.trim()||referenceMessageId){setRequestChanged(true);setValue(undefined);}},[rawText,referenceMessageId,referenceClauseId]);
 useEffect(()=>{setRequestChanged(true);},[mode,targetLanguage,productId,sessionId]);
 useEffect(()=>{if(busy)setRequestChanged(false);},[busy]);
 const [value,setValue]=useState<Data>();const [error,setError]=useState('');const [loading,setLoading]=useState(false);const [job,setJob]=useState<string>();const [open,setOpen]=useState(false);
 const [visible,setVisible]=useState(()=>!document.hidden);
 useEffect(()=>{const update=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',update);return()=>document.removeEventListener('visibilitychange',update);},[]);
 useEffect(()=>{setValue(undefined);setJob(undefined);setError('');setLoading(false);},[sessionId,productId]);
 useEffect(()=>{setValue(undefined);setError('');},[mode,runId]);
 useEffect(()=>{
  if(!sessionId||!productId||!active||!visible){setLoading(false);return;}
  let live=true,inFlight=false;
  const load=()=>{if(inFlight||document.hidden)return;inFlight=true;setLoading(true);const path=`/sessions/${sessionId}/context?product_id=${encodeURIComponent(productId)}&raw_text=${encodeURIComponent(rawText)}&mode=${encodeURIComponent(mode)}&target_language=${targetLanguage}${referenceMessageId?'&reference_message_id='+encodeURIComponent(referenceMessageId):''}${referenceClauseId?'&reference_clause_id='+encodeURIComponent(referenceClauseId):''}`;
   api(path).then(data=>{if(live){setValue(data);setError('');}}).catch(e=>{if(live)setError(e.message);}).finally(()=>{inFlight=false;if(live)setLoading(false);});};
  const timer=window.setTimeout(load,300),poll=open?window.setInterval(load,1800):undefined;
  return()=>{live=false;window.clearTimeout(timer);window.clearInterval(poll);};
 },[sessionId,productId,rawText,referenceMessageId,referenceClauseId,targetLanguage,busy,open,job,mode,runId,active,visible]);
 useEffect(()=>{
  if(!job||!open||!active||!visible)return;let live=true;
  const timer=window.setInterval(()=>{if(document.hidden)return;api(`/jobs/${job}`).then(data=>{if(!live)return;const state=data.status??data.job?.status;if(!['queued','running'].includes(state)){if(state!=='completed')setError(data.error?.message??'压缩未完成，原历史已保留');setJob(undefined);}}).catch(e=>{if(live){setError(e.message);setJob(undefined);}});},1500);
  return()=>{live=false;window.clearInterval(timer);};
 },[job,open,active,sessionId,productId,visible]);
 async function compress(){if(!sessionId||!productId)return;setLoading(true);setError('');try{const result=await api(`/sessions/${sessionId}/context/compress`,'POST',{product_id:productId});if(currentScope.current===scope)setJob(result.job_id);}catch(e){if(currentScope.current===scope)setError((e as Error).message);}finally{if(currentScope.current===scope)setLoading(false);}}
 const actual=(!requestChanged||busy)&&(value?.last_request??value?.prepared);const preview=actual||value?.preview;
 const input=preview?.input_tokens,reserve=preview?.output_reserved_tokens,windowSize=preview?.window;
 const ratio=(n:unknown)=>finiteBudget(n)&&finiteBudget(windowSize)&&windowSize>0&&Number.isFinite(n/windowSize*100)?n/windowSize*100:null;
 const total=finiteBudget(input)&&finiteBudget(reserve)?ratio(input+reserve):null;
 const inputRatio=ratio(input);
 const historyCount=Array.isArray(value?.preview?.message_ids)?value.preview.message_ids.length:null;
 const triggerLabel=loading?'上下文输入占用加载中':error?'上下文输入占用加载失败':inputRatio===null?'上下文输入占用尚未知':`上下文输入占用${Math.round(inputRatio)}%`;
 const formatted=(n:number|null)=>n===null?'—':`${Math.round(n)}%`;
 const reason=busy||value?.busy?'任务执行中':!value?.can_compress?'暂无可压缩历史':'';
 const content=<div className="context-window">
  <div className="context-heading"><strong>上下文窗口</strong><span tabIndex={0} title={`${preview?.model??'未知模型'} / ${count(windowSize)} Token`}>输入占用 {formatted(inputRatio)}</span></div>
  {actual?<p>最近一次请求 · {preview?.counting==='exact'?'输入实测':'输入估算'}，回复预留单独列出<br/>{preview?.clause_number?`第 ${preview.clause_number} 条 · `:''}{label(preview?.logical_node_name??preview?.node??'')||'处理任务'}</p>:<p>下一次请求的预估占用，圆环仅显示输入。<br/>{mode==='TRANSLATION'?'文本翻译不检索产品证据。':'暂不含待检索资料。'}</p>}
  <div className="context-meter" role="img" aria-label={`输入 ${formatted(inputRatio)}，回复预留 ${formatted(ratio(reserve))}`}><i style={{width:`${Math.min(100,ratio(input)??0)}%`}}/><i style={{width:`${Math.min(Math.max(0,100-(ratio(input)??0)),ratio(reserve)??0)}%`}}/></div>
  <div className="context-category"><span><i/>输入内容</span><span>{count(input)} Token · {formatted(inputRatio)}</span></div>
  <div className="context-category reserve"><span><i/>回复预留</span><span>{count(reserve)} Token · {formatted(ratio(reserve))}</span></div>
  <div className="context-category"><span>当前对话历史</span><span>{historyCount===null?'未知':`${historyCount} 条消息`}</span></div>
  <p>输入包含系统指令与格式要求，新对话也会占用少量空间。回复预留尚未实际使用。</p>
  {total!==null&&total>100&&<p role="alert">含回复预留的预估总量超过窗口容量，请缩短输入或压缩历史。</p>}
  {error&&<p role="alert">{error}</p>}
  <div title={reason}><Button aria-label="压缩上下文" block icon={<CompressOutlined/>} onClick={compress} loading={Boolean(job)||loading} disabled={busy||value?.busy||loading||!value?.can_compress||Boolean(job)}>{job?'压缩中…':'压缩上下文'}</Button></div>
  {reason&&<small className="context-disabled-reason">{reason}</small>}
 </div>;
 return <Popover content={content} trigger="click" open={open} onOpenChange={setOpen}><Tooltip title={triggerLabel} trigger={['hover','focus']}><Button className="context-ring-trigger" type="text" aria-label={triggerLabel} aria-expanded={open} aria-haspopup="dialog" aria-busy={loading||Boolean(job)} style={{width:32,height:32,minWidth:32,padding:0,flexShrink:0,borderRadius:'50%'}} disabled={!sessionId||!productId} onKeyDown={event=>{if(event.key==='Escape'){setOpen(false);event.stopPropagation();}else if(event.key==='Enter'||event.key===' '){event.preventDefault();setOpen(value=>!value);}}}>
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="7" stroke="#d8dee8" strokeWidth="2"/>{inputRatio!==null&&!error&&<circle data-context-progress cx="9" cy="9" r="7" stroke={total!==null&&total>100?'#dc684c':'#6e8ba6'} strokeWidth="2" pathLength="100" strokeDasharray="100" strokeDashoffset={100-Math.min(100,Math.max(0,inputRatio))} transform="rotate(-90 9 9)" strokeLinecap="round"/>}</svg>
 </Button></Tooltip></Popover>;
}
