import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Collapse,Input,InputNumber,Select,Space,Tag} from 'antd';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';
const keyLinks:Record<string,string>={deepseek:'https://platform.deepseek.com/api_keys',glm:'https://bigmodel.cn/usercenter/proj-mgmt/apikeys',doubao:'https://ark.volcengine.com/region:cn-beijing/apikey'};
const labels:Record<string,string>={internal:'内网API',deepseek:'DeepSeek',doubao:'豆包',glm:'智谱 GLM'};
const internalPortal='https://maas-openclaw.zte.com.cn/zte-paas-coclaw-ui/#/getkey';
function normalizedFields(id:string,p:Data,key?:string){
 let base=p.base_url.trim();
 if(id==='internal'&&base.replace(/\/+$/,'').endsWith('/chat/completions'))base=base.replace(/\/+$/,'').slice(0,-'/chat/completions'.length);
 return {base_url:base,model_id:p.model_id.trim(),timeout:Number(p.timeout),max_output_tokens:Number(p.max_output_tokens),context_limit:Number(p.context_limit),structured_output_mode:p.structured_output_mode,
  ...(['glm','deepseek'].includes(id)?{structured_thinking:p.structured_thinking??'auto'}:{}),...(key?.trim()?{key:key.trim()}:{})};
}
export default function GenerationProfiles({initial}:{initial:Data}){
 const [config,setConfig]=useState(initial),[saved,setSaved]=useState(initial),[editing,setEditing]=useState(initial.active_generation_profile_id??'deepseek'),[keys,setKeys]=useState<Data>({}),[busy,setBusy]=useState(''),[error,setError]=useState<unknown>(null),[message,setMessage]=useState(''),[checks,setChecks]=useState<Data>({}),[undo,setUndo]=useState<{id:string;profile:Data}|null>(null);
 const request=useRef<AbortController|null>(null),epoch=useRef(0),locked=useRef(false);
 const [internalEnabled,setInternalEnabled]=useState(false);
 const [executionBusy,setExecutionBusy]=useState(false),executionLock=useRef(false);
 useEffect(()=>{const pending=(event:Event)=>{executionLock.current=!!(event as CustomEvent).detail;setExecutionBusy(executionLock.current);};window.addEventListener('laura:execution-busy',pending);return()=>window.removeEventListener('laura:execution-busy',pending);},[]);
 useEffect(()=>()=>{epoch.current++;request.current?.abort();},[]);
 const invalidate=()=>{epoch.current++;if(request.current){request.current.abort();request.current=null;locked.current=false;}};
 const change=(id:string,key:string,value:unknown)=>{setUndo(null);setConfig((old:Data)=>({...old,profiles:{...old.profiles,[id]:{...old.profiles[id],[key]:value}}}));};
 const dirty=(id:string)=>!!keys[id]||JSON.stringify(config.profiles[id])!==JSON.stringify(saved.profiles[id]);
 function reset(){if(!config.defaults?.[editing]||(busy&&busy!=='check'))return;if(dirty(editing)&&!window.confirm('恢复默认设置会替换此接口的全部未保存字段，并清除未保存的新密钥。保留已保存密钥，点击保存后生效。确定替换草稿？'))return;invalidate();setBusy('');setChecks({});setError(null);setUndo({id:editing,profile:{...config.profiles[editing]}});const defaults=config.defaults[editing];const fields=Object.fromEntries(['base_url','model_id','timeout','max_output_tokens','context_limit','structured_output_mode','structured_thinking'].filter(k=>k in defaults).map(k=>[k,defaults[k]]));setConfig((old:Data)=>({...old,profiles:{...old.profiles,[editing]:{...old.profiles[editing],...fields}}}));setKeys(old=>({...old,[editing]:''}));setMessage(`${labels[editing]}已回填出厂默认草稿，尚未保存；保留已保存密钥，已清除未保存的新密钥。地址域名变化时，保存后需为新地址重新填写密钥。`);}
 function undoReset(){if(!undo||undo.id!==editing)return;invalidate();setBusy('');setChecks({});setConfig((old:Data)=>({...old,profiles:{...old.profiles,[editing]:undo.profile}}));setUndo(null);setMessage('已撤销字段恢复，尚未保存；未保存的新密钥保持清空。');}
 async function save(id:string,activate=false,enableNetwork=false){
  if(locked.current||(enableNetwork&&executionLock.current))return;
  const fields=normalizedFields(id,config.profiles[id],keys[id]);
  if(enableNetwork){
   if(!fields.base_url||!fields.model_id||(!fields.key&&!saved.profiles[id]?.key_configured)){setError(new Error('请先填写内网API地址、模型ID和Key。'));return;}
   try{
    if(!fields.key&&new URL(fields.base_url).host!==new URL(saved.profiles[id].base_url).host){setError(new Error('地址域名变化，请为新地址重新填写Key。'));return;}
   }catch{setError(new Error('请填写有效的内网API地址。'));return;}
   if(!window.confirm(`保存并启用内网API将开启全局生成API联网，并设当前接口为：${fields.base_url}（模型 ${fields.model_id}）。生成问答和SOC将向该地址发送生成内容，可能包含条款与引用证据。确认后保存字段并启用？`))return;
  }
  invalidate();locked.current=true;const generation=epoch.current;setChecks({});setBusy(id);setError(null);setMessage('');let fieldsSaved=false;
  if(enableNetwork)window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:true}));
  try{
   const value=await api('/settings/models','PUT',{expected_version:config.config_version,profiles:{[id]:fields},...(activate?{active_generation_profile_id:id}:{})});
   if(epoch.current!==generation)return;
   fieldsSaved=true;setUndo(null);setSaved(value);setConfig((old:Data)=>({...value,profiles:{...old.profiles,[id]:value.profiles[id]}}));setKeys(old=>({...old,[id]:''}));
   if(enableNetwork){
    const execution=await api('/settings/execution');
    if(epoch.current!==generation)return;
    const updated=await api('/settings/execution','PUT',{expected_version:execution.version,model_mode:'cloud',network_allowed:true});
    window.dispatchEvent(new CustomEvent('laura:execution-updated',{detail:updated}));
    if(epoch.current!==generation)return;
    setInternalEnabled(true);setMessage('内网API配置已保存并设为当前接口；全局生成API联网已开启。尚未校验账户连通性。');
   }else setMessage(`${labels[id]}配置已保存${activate?'并设为当前接口':''}；其他接口的未保存内容仍保留。`);
  }catch(e){if(epoch.current===generation){setError(e);if(enableNetwork)setMessage(fieldsSaved?'内网API配置已保存并设为当前接口；开启全局生成API联网未完成，请检查联网设置后重试。':'保存内网API配置未完成，尚未执行启用联网步骤。');}}
  finally{if(enableNetwork)window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:false}));if(epoch.current===generation){locked.current=false;setBusy('');}}
 }
 async function select(id:string){if(locked.current)return;locked.current=true;invalidate();setChecks({});setBusy('select');setError(null);try{const value=await api('/settings/models','PUT',{expected_version:config.config_version,active_generation_profile_id:id});setUndo(null);setSaved(value);setConfig((old:Data)=>({...value,profiles:old.profiles}));setMessage('当前生成接口已保存；历史和已入队任务继续使用原配置。');}catch(e){setError(e);}finally{locked.current=false;setBusy('');}}
 async function allowNetworkAndCheck(id:string){
  if(locked.current||executionLock.current)return;
  invalidate();locked.current=true;const generation=epoch.current;
  setBusy('network');setError(null);setMessage('');let allowed=false;
  window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:true}));
  try{
   const execution=await api('/settings/execution');
   if(epoch.current!==generation)return;
   const updated=await api('/settings/execution','PUT',{expected_version:execution.version,model_mode:'cloud',network_allowed:true});
   window.dispatchEvent(new CustomEvent('laura:execution-updated',{detail:updated}));allowed=true;
  }catch(e){if(epoch.current===generation){setError(e);setMessage('开启全局生成API联网未完成，尚未重新校验连接。');}}
  finally{
   window.dispatchEvent(new CustomEvent('laura:execution-busy',{detail:false}));
   if(epoch.current===generation){locked.current=false;setBusy('');}
  }
  if(allowed&&epoch.current===generation)void check(id);
 }
 async function check(id:string,mode='models'){
  if(locked.current)return;
  invalidate();locked.current=true;
  const generation=epoch.current,controller=new AbortController(),seconds=mode==='generation'?30:15;
  request.current=controller;setBusy('check');setError(null);setChecks(old=>({...old,[id]:null}));
  let timer:ReturnType<typeof setTimeout>;
  const timeoutMessage=`校验超时（${seconds} 秒），请稍后重试`;
  const deadline=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error(timeoutMessage));},seconds*1000);});
  try{
   const result=await Promise.race([api('/settings/models/check','POST',{profile_id:id,...(mode==='generation'?{mode}:{})},undefined,{signal:controller.signal}),deadline]);
   if(epoch.current===generation)setChecks(old=>({...old,[id]:result}));
  }catch(e){
   if(epoch.current===generation)setChecks(old=>({...old,[id]:{connected:false,error_code:(e as Data)?.code,message:controller.signal.aborted?timeoutMessage:e instanceof Error?e.message:'校验请求未完成'}}));
  }finally{clearTimeout(timer!);if(epoch.current===generation){setBusy('');request.current=null;locked.current=false;}}
 }
 const p=config.profiles[editing],name=labels[editing],result=checks[editing],writing=!!busy&&busy!=='check';
 return (<>
 <Alert type="info" title="只向当前生成接口发送请求，失败不会自动切换供应商；向量化与检索继续在本地。"/>
 <ErrorNotice error={error}/>{message&&<p role="status">{message}</p>}<p>当前生成接口：<strong>{labels[config.active_generation_profile_id]??'未选择'}</strong> · 已保存版本 {config.config_version}</p>
 <div className="generation-master-detail">
 <nav className="generation-interface-list" aria-label="编辑接口">{Object.entries(labels).filter(([id])=>config.profiles[id]).map(([id,label])=>
 <button key={id} disabled={!!busy&&busy!=='check'} aria-current={editing===id?'page':undefined} onClick={()=>{invalidate();setBusy('');setEditing(id);}}>
 <strong>{label}</strong>
 <small>{config.active_generation_profile_id===id?'当前生成接口 · ':''}{dirty(id)?'有未保存草稿':'已保存'}</small>
 </button>)}</nav>
 <Select className="generation-mobile-selector" disabled={!!busy&&busy!=='check'} aria-label="编辑接口" value={editing} options={Object.entries(labels).filter(([id])=>config.profiles[id]).map(([value,label])=>({value,label}))} onChange={id=>{invalidate();setBusy('');setEditing(id);}}/>{p&&<section role="region" aria-label={name} className="generation-profile-editor">
 <div className="section-bar">
 <h3>{name}</h3>
 <Space>
 <Tag>{dirty(editing)?'未保存草稿':'已保存'}</Tag>
 <Button disabled={!!busy||config.active_generation_profile_id===editing} onClick={()=>select(editing)}>设为当前接口</Button>
 </Space>
 </div>
 <label>API 地址<Input disabled={writing} aria-label={`${name} API 地址`} type="url" value={p.base_url} onChange={e=>change(editing,'base_url',e.target.value)}/>
 </label>
 <label>模型 ID<Input disabled={writing} aria-label={`${name} 模型 ID`} value={p.model_id} onChange={e=>change(editing,'model_id',e.target.value)}/>
 </label>
 <label>API 密钥<Input.Password disabled={writing} aria-label={`${name} API 密钥`} autoComplete="new-password" value={keys[editing]??''} onChange={e=>setKeys(old=>({...old,[editing]:e.target.value}))} placeholder={p.key_configured?'留空保留已保存密钥':'密钥仅服务端保存，不回显'}/>
 </label>
 <p className="source-version">{keyLinks[editing]?<>在官方平台<a href={keyLinks[editing]} target="_blank" rel="noopener noreferrer">申请 API Key</a>；部分模型需另行开通权限或填写账户专属模型 ID。</>:<>点击 <a href={internalPortal} target="_blank" rel="noopener noreferrer">获取URL、模型ID和Key</a>，按对应字段复制粘贴。支持标准 /chat/completions 完整地址，保存时仅去除该后缀。</>}</p>
 <Space wrap>
 <Button disabled={!!busy&&busy!=='check'||!config.defaults?.[editing]} onClick={reset}>恢复默认设置</Button>{undo?.id===editing&&<Button disabled={!!busy} onClick={undoReset}>撤销恢复</Button>}<Button type="primary" loading={busy===editing} disabled={!!busy} onClick={()=>save(editing)}>保存此接口</Button>{editing==='internal'&&!internalEnabled?<Button disabled={!!busy||executionBusy} onClick={()=>save(editing,true,true)}>保存并启用内网API</Button>:<Button disabled={!!busy} onClick={()=>save(editing,true)}>保存并设为当前接口</Button>}<Button loading={busy==='check'} disabled={!!busy} onClick={()=>check(editing)}>{busy==='check'?'正在校验…':'校验已保存连接'}</Button>
 </Space>{dirty(editing)&&<p className="source-version">存在未保存草稿；校验仅使用服务端已保存配置，不会自动保存。</p>}<div role="status" aria-live="polite">{busy==='check'?'正在校验已保存连接…':result&&<Alert type={result.connected?'success':'warning'} title={result.message} description={result.checked_at?`接口 ${result.profile_id} · 配置版本 ${result.config_version} · ${result.checked_at}`:undefined}/>}</div>{result?.error_code==='EXECUTION_OFFLINE'&&<div className="notice">
 <p>将开启全局生成API联网，并校验已保存的接口：{saved.profiles[editing]?.base_url}。生成业务任务仍使用当前接口：{saved.profiles[config.active_generation_profile_id]?.base_url||'尚未配置'}，可能向该地址发送条款与引用证据。</p>
 <Button disabled={!!busy||executionBusy} onClick={()=>allowNetworkAndCheck(editing)}>允许生成 API 联网并重新校验</Button>
 </div>}{result?.model_list_supported===false&&<div>
 <p>可主动发送固定短测试消息验证生成能力，可能产生少量费用，不包含业务资料。</p>
 <Button disabled={!!busy} onClick={()=>check(editing,'generation')}>发送测试消息（可能计费）</Button>
 </div>}<Collapse items={[{key:'advanced',label:'高级参数',children:<>
 <Space wrap>
 <label>单次超时（秒）<InputNumber disabled={writing} min={1} max={600} value={p.timeout} onChange={v=>change(editing,'timeout',v)}/>
 </label>
 <label>输出 Token 上限<InputNumber disabled={writing} min={256} value={p.max_output_tokens} onChange={v=>change(editing,'max_output_tokens',v)}/>
 </label>
 <label>上下文窗口<InputNumber disabled={writing} min={1024} value={p.context_limit} onChange={v=>change(editing,'context_limit',v)}/>
 </label>
 </Space>{['glm','deepseek'].includes(editing)&&<label>思考模式<Select disabled={writing} aria-label={`${name} 思考模式`} value={p.structured_thinking??'auto'} options={[{value:'disabled',label:'关闭思考'},{value:'enabled',label:'开启思考'},{value:'auto',label:'供应商默认'}]} onChange={v=>change(editing,'structured_thinking',v)}/>
 </label>}<label>结构化输出<Select disabled={writing} aria-label={`${name} 结构化输出`} value={p.structured_output_mode} options={[{value:'json_object',label:'JSON 模式'},{value:'none',label:'由提示词约束 JSON'}]} onChange={v=>change(editing,'structured_output_mode',v)}/>
 </label>
 </>}]}/>
 <p className="source-version">连通检查通过仅说明接口响应情况，不代表生成质量已验收。</p>
 </section>}</div>
 </>);
}
