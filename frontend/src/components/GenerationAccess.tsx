import {useEffect,useState} from 'react';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';

export default function GenerationAccess(){
 const [execution,setExecution]=useState<Data|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<any>(null),[message,setMessage]=useState(''),[savedAllowed,setSavedAllowed]=useState<boolean|null>(null);
 useEffect(()=>{let alive=true;api('/settings/execution').then(value=>{if(alive){setExecution(value);setSavedAllowed(value.network_allowed);}}).catch(e=>{if(alive)setError(e);});return()=>{alive=false;};},[]);
 useEffect(()=>{const updated=(event:Event)=>{const value=(event as CustomEvent).detail;setExecution(value);setSavedAllowed(value.network_allowed);};window.addEventListener('laura:execution-updated',updated);return()=>window.removeEventListener('laura:execution-updated',updated);},[]);
 const save=async()=>{
  if(!execution)return;
  setBusy(true);setError(null);setMessage('');
  try{
   const saved=await api('/settings/execution','PUT',{expected_version:execution.version,model_mode:'cloud',network_allowed:execution.network_allowed});setExecution(saved);setSavedAllowed(saved.network_allowed);
   setMessage('联网设置已保存；生成使用所选 API，本地 Embedding 保持不变。');
  }catch(e){setError(e);}finally{setBusy(false);}
 };
 return <section className="panel generation-access-compact" aria-label="生成 API 联网权限"><h2>生成 API 联网权限</h2><small>{execution&&execution.network_allowed!==savedAllowed?'未保存草稿':'已保存'} · 已保存联网权限：{savedAllowed?'允许':'关闭'}</small>
  <ErrorNotice error={error}/>{message&&<p role="status">{message}</p>}
  <p className="muted">生成问答和 SOC 需要访问所选 API，可能发送条款与引用证据。本地建库、检索及历史查看无需生成 API。关闭联网会阻止后续云端请求；已发出的请求无法撤回。LangSmith 使用独立开关。</p>
  {execution&&<><label><input type="checkbox" checked={execution.network_allowed} onChange={e=>setExecution({...execution,network_allowed:e.target.checked})}/>允许生成 API 联网</label>
   <button type="button" disabled={busy} onClick={save}>{busy?'正在保存…':'保存联网设置'}</button>
  </>}
 </section>;
}
