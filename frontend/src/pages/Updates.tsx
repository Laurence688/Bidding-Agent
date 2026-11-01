import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Modal,Progress,Space,Switch,Tag} from 'antd';
import {api,Data} from '../api/client';
import {ErrorNotice} from '../components/Status';

const busyPhases=new Set(['checking','downloading','verifying']);
export default function Updates(){
 const [state,setState]=useState<Data|null>(null),[error,setError]=useState<unknown>(null);
 const [busy,setBusy]=useState(false),[restarting,setRestarting]=useState(false),[missing,setMissing]=useState(false);
 const alive=useRef(true),request=useRef(false),revision=useRef(0);
 useEffect(()=>{
  alive.current=true;let disposed=false,timer:ReturnType<typeof setTimeout>;
  const refresh=async()=>{
   let retry=true;const before=revision.current;
   try{
    const value=await api('/updates');
    if(!disposed&&!request.current&&before===revision.current){
     setState(value);setError(null);setMissing(false);
     if(['failed','completed'].includes(value.phase))setRestarting(false);
    }
   }catch(e){
    if(!disposed&&!restarting){
     if((e as {status?:number})?.status===404){setMissing(true);setError(null);retry=false;}
     else setError(e);
    }
   }finally{if(!disposed&&retry)timer=setTimeout(refresh,1000);}
  };
  void refresh();return()=>{disposed=true;alive.current=false;clearTimeout(timer);};
 },[restarting]);
 async function action(path:string,body:Data,method='POST'){
  if(request.current||restarting||!state)return;
  if(path!=='/updates/preferences'&&(!state.supported||!state.enabled))return;
  request.current=true;revision.current++;setBusy(true);setError(null);
  try{const value=await api(path,method,body);if(alive.current){setState(value);if(path.endsWith('/install'))setRestarting(true);}}
  catch(e){if(alive.current)setError(e);}
  finally{request.current=false;if(alive.current)setBusy(false);}
 }
 const check=()=>Modal.confirm({title:'检查新版',content:'将连接本项目的 GitHub 更新服务，仅查询版本信息，不会下载或安装。',okText:'检查版本',cancelText:'取消',onOk:()=>action('/updates/check',{allow_check:true})});
 const download=()=>Modal.confirm({title:'下载新版',content:'下载并校验你选择的新版，下载完成后仍需手动点击“安装并重启”，不会自动安装。',okText:'确认下载',cancelText:'取消',onOk:()=>action('/updates/download',{allow_download:true})});
 const install=()=>Modal.confirm({title:'自动备份、安装并重启',content:'请先保存当前输入草稿。系统会保留知识库、对话、记忆、模型与配置并自动备份；有排队或运行任务时暂不安装。更新期间服务会短暂停止，完成后自动打开 Laura。',okText:'安装并重启',cancelText:'取消',onOk:()=>action('/updates/install',{confirm_restart:true})});
 const active=busyPhases.has(state?.phase),enabled=state?.enabled===true;
 const disabled=!enabled||!state?.supported||active||busy||restarting||state?.phase==='installing';
 const percent=state?.total?Math.min(100,Math.floor(100*(state.completed??0)/state.total)):undefined;
 return <section>
  <div className="page-heading"><div><p className="eyebrow">系统设置</p><h1>检查更新</h1><p>手动检查、下载和安装；安装前自动备份资料与设置。</p></div><Tag>当前版本 {state?.installed_version??(missing?'后台版本未读取':'正在读取')}</Tag></div>
  {missing&&<Alert type="warning" title="本地后台尚未加载更新接口" description="网页已更新，后台仍是旧版本。保存当前输入后重新启动Laura，再刷新页面；无需重新配置模型或重建知识库。" action={<Button onClick={()=>window.location.reload()}>刷新页面</Button>}/>}
  <ErrorNotice error={error} onDismiss={()=>setError(null)}/>
  {state&&<div className="panel">
   <Space wrap><Switch aria-label="启用更新功能" checked={enabled} disabled={busy||restarting||state.phase==='installing'} onChange={value=>void action('/updates/preferences',{enabled:value},'PUT')}/><strong>启用更新功能</strong><Tag>{enabled?'已开启':'已关闭'}</Tag></Space>
   <p className="muted">默认关闭。打开后也不会后台检查、自动下载或自动安装；每一步都由你手动确认。关闭开关会暂停未完成的下载。</p>
   <h2>{state.latest_version?`可用版本 ${state.latest_version}`:'Laura 程序更新'}</h2>
   {!state.supported&&<Alert type="info" title="当前为开发目录" description="开发分支通过代码管理更新，不接受便携包覆盖。便携版用户可在此手动检查更新，或运行包内的“检查更新Laura.cmd”。"/>}
   <p role="status">{restarting?'正在自动备份和重启，请等待新版打开；下载文件与备份会保留。':state.message}</p>
   {active&&<Progress percent={percent} status="active" format={p=>state.phase==='verifying'?'正在校验':`${p??0}%`}/>}
   <Space wrap>
    <Button type="primary" disabled={disabled} onClick={check}>检查更新</Button>
    {['available','paused'].includes(state.phase)&&<Button disabled={disabled} onClick={download}>{state.phase==='paused'?'继续下载':'下载新版'}</Button>}
    {active&&state.phase==='downloading'&&<Button disabled={busy} onClick={()=>void action('/updates/pause',{})}>暂停下载</Button>}
    {state.phase==='ready'&&<Button type="primary" disabled={disabled||!state.can_install} onClick={install}>安装并重启</Button>}
   </Space>
   {state.phase==='ready'&&!state.can_install&&enabled&&<p>新版已就绪。请关闭 Laura 后，在原目录运行“检查更新Laura.cmd”，按提示确认安装。</p>}
   {state.notes&&<details><summary>版本说明</summary><pre style={{whiteSpace:'pre-wrap'}}>{state.notes}</pre></details>}
   <p className="muted">安装前自动备份到原目录的 .laura-backups，保留知识库、对话、记忆和模型设置。检查与下载需要能访问 GitHub。</p>
  </div>}
 </section>;
}
