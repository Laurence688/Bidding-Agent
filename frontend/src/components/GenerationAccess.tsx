import {useEffect,useRef,useState} from 'react';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';

export default function GenerationAccess(){
 const [execution,setExecution]=useState<Data|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<any>(null),[message,setMessage]=useState(''),[savedAllowed,setSavedAllowed]=useState<boolean|null>(null);
 const [externalBusy,setExternalBusy]=useState(false),version=useRef(-1),locked=useRef(false),externalLock=useRef(false);
 const accept=(value:Data)=>{if(value.version<=version.current)return false;version.current=value.version;setExecution(value);setSavedAllowed(value.network_allowed);return true;};
 useEffect(()=>{let alive=true;api('/settings/execution').then(value=>{if(alive)accept(value);}).catch(e=>{if(alive)setError(e);});return()=>{alive=false;};},[]);
 useEffect(()=>{
  const updated=(event:Event)=>{if(accept((event as CustomEvent).detail)){setMessage('');setError(null);}};
  const pending=(event:Event)=>{externalLock.current=!!(event as CustomEvent).detail;setExternalBusy(externalLock.current);};
  window.addEventListener('laura:execution-updated',updated);window.addEventListener('laura:execution-busy',pending);
  return()=>{window.removeEventListener('laura:execution-updated',updated);window.removeEventListener('laura:execution-busy',pending);};
 },[]);
 const save=async()=>{
  if(!execution||locked.current||externalLock.current)return;
  locked.current=true;window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:true}));
  setBusy(true);setError(null);setMessage('');
  try{
   const saved=await api('/settings/execution','PUT',{expected_version:execution.version,model_mode:'cloud',network_allowed:execution.network_allowed});
   if(accept(saved)){window.dispatchEvent(new CustomEvent('laura:execution-updated',{detail:saved}));setMessage('联网设置已保存；生成使用所选 API，本地 Embedding 保持不变。');}
  }catch(e){setError(e);}finally{locked.current=false;setBusy(false);window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:false}));}
 };
 return <section className="panel generation-access-compact" aria-label="生成 API 联网权限"><h2>生成 API 联网权限</h2><small>{execution&&execution.network_allowed!==savedAllowed?'未保存草稿':'已保存'} · 已保存联网权限：{savedAllowed?'允许':'关闭'}</small>
  <ErrorNotice error={error}/>{message&&<p role="status">{message}</p>}
  <p className="muted">生成问答和 SOC 需要访问所选 API，可能发送条款与引用证据。本地建库、检索及历史查看无需生成 API。关闭联网会阻止后续云端请求；已发出的请求无法撤回。LangSmith 使用独立开关。</p>
  {execution&&<><label><input type="checkbox" disabled={busy||externalBusy} checked={execution.network_allowed} onChange={e=>setExecution({...execution,network_allowed:e.target.checked})}/>允许生成 API 联网</label>
   <button type="button" disabled={busy||externalBusy} onClick={save}>{busy?'正在保存…':'保存联网设置'}</button>
  </>}
 </section>;
}
