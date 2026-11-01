import {productLabel} from '../components/knowledgeModel';
import {Button,Input,Select,Tag,Collapse,Modal} from 'antd';
import {ArrowUpOutlined,PlusOutlined,DatabaseOutlined,MessageOutlined,FileDoneOutlined,CopyOutlined,EditOutlined,CheckOutlined} from '@ant-design/icons';
import ProjectSelector from '../components/ProjectSelector';
import ContextUsage from '../components/ContextUsage';
import ComposerMode from '../components/ComposerMode';
import ResponseBlock from '../components/ResponseBlock';
import RunActivity from '../components/RunActivity';
import BatchReply from '../components/BatchReply';
import {useEffect,useRef,useState} from 'react';
import {api,Data,label} from '../api/client';
import {Badge,ErrorNotice} from '../components/Status';
import ReviewFeedbackCard from '../components/ReviewFeedbackCard';
import ExcelUpload from '../components/ExcelUpload';
import {Icon} from '../components/Icon';
import './conversation.css';

type Props={projects?:Data[];onChooseProject?:(id:string)=>void;onManageProjects?:()=>void;onCreateProject?:()=>void;active?:boolean;project:Data|null;products:Data[];session:Data|null;onProjectsChanged:()=>Promise<void>;onTask:(id:string,runId?:string,clauseId?:string)=>void;onNewSession:()=>Promise<Data|undefined>;onSessionChanged:(s:Data|null)=>void};
type Draft={text:string;product:string;mode:string;file:File|null;targetLanguage?:string;referenceMessageId?:string;referenceClauseId?:string};
const drafts=new Map<string,Draft>();
const requestKeys=new WeakMap<Draft,string>();
const emptyDraft=(product:string):Draft=>({text:'',product,mode:'PRODUCT_QA',file:null});

export default function Workspace({projects=[],onChooseProject,onManageProjects,onCreateProject,active=true,project,products,session,onProjectsChanged,onTask,onNewSession}:Props){
 const key=`${project?.id??'none'}:${session?.id??'new'}`;
 const [draftState,setDraftState]=useState<{key:string;value:Draft}>(()=>({key,value:drafts.get(key)??emptyDraft(project?.default_product_id??products[0]?.id??'')}));
 const draft=draftState.key===key?draftState.value:drafts.get(key)??emptyDraft(project?.default_product_id??products[0]?.id??'');
 const context=useRef(key);context.current=key;
 const [messages,setMessages]=useState<Data[]>([]),[older,setOlder]=useState<string|null>(null),[error,setError]=useState<unknown>(null),[busy,setBusy]=useState(false),[loadingOlder,setLoadingOlder]=useState(false),[name,setName]=useState('');
 const sending=useRef(false),composing=useRef(false),historyLoaded=useRef(false),generation=useRef(0),fileInput=useRef<HTMLInputElement>(null),bottom=useRef<HTMLDivElement>(null);
 const localKey=useRef(crypto.randomUUID()),uploaded=useRef<{file:File;id:string}|null>(null);
 function change(patch:Partial<Draft>){localKey.current=crypto.randomUUID();const value={...draft,...patch};drafts.set(key,value);setDraftState({key,value});setError(null);}
 function referenceMessage(message:Data,referenceClauseId?:string){const apply=()=>change({referenceClauseId,mode:'TRANSLATION',text:'翻译所选回答',referenceMessageId:message.id,file:null});if(draft.text||draft.file)Modal.confirm({title:'替换当前未发送的草稿？',content:'所选消息将作为翻译原文，历史记录保留。',okText:'引用并替换',cancelText:'保留草稿',onOk:apply});else apply();}
 useEffect(()=>{if(draft.targetLanguage)return;const prior=[...messages].reverse().find(m=>m.mode==='TRANSLATION'&&m.target_language&&m.parent_id===session?.id);if(prior){const value={...draft,targetLanguage:prior.target_language};drafts.set(key,value);setDraftState({key,value});}},[messages,key]);
 function combine(fresh:Data[],old:Data[]){const items=new Map(old.map(m=>[m.id,m]));fresh.forEach(m=>{if(m.role==='assistant'&&m.run_id)for(const [id,prior] of items){if(prior.role==='assistant'&&prior.run_id===m.run_id){if(m.type==='progress'&&['qa_result','soc_result','batch_result','translation_result'].includes(prior.type))return;items.delete(id);}}items.set(m.id,m);});return [...items.values()].sort((a,b)=>a.order_key&&b.order_key?(a.order_key[0]-b.order_key[0]||a.order_key[1]-b.order_key[1]):String(a.created_at??'').localeCompare(String(b.created_at??'')));}
 async function refreshRun(runId:string){const target=session?.id,scope=key,ticket=generation.current;if(!target)return;const result=await api(`/sessions/${target}/messages?run_id=${encodeURIComponent(runId)}`);if(ticket!==generation.current||scope!==context.current)return;setMessages(old=>combine(result.items,old));}
 function editMessage(m:Data){const apply=()=>{change({text:m.original_text??m.text,product:m.product_id??draft.product,mode:m.mode==='TRANSLATION'?'TRANSLATION':m.mode==='PRODUCT_QA'?'PRODUCT_QA':'SOC',targetLanguage:m.target_language??draft.targetLanguage,referenceMessageId:undefined,referenceClauseId:undefined,file:null});document.querySelector<HTMLTextAreaElement>('[aria-label="输入消息"]')?.focus();};if(draft.text||draft.file)Modal.confirm({title:'替换当前未发送的草稿？',content:'历史消息会填回输入框，原消息和回答保留。',okText:'替换草稿',cancelText:'保留草稿',onOk:apply});else apply();}
 async function refresh(target=session?.id,scope=key){if(!target)return;const ticket=generation.current;const result=await api(`/sessions/${target}/messages`);if(ticket!==generation.current||context.current!==scope)return;setMessages(old=>combine(result.items,old));if(!historyLoaded.current)setOlder(result.older_cursor??result.next_cursor??null);}
 useEffect(()=>{generation.current++;setMessages([]);setOlder(null);setError(null);historyLoaded.current=false;localKey.current=crypto.randomUUID();},[key]);
 useEffect(()=>{if(!active||!session)return;let alive=true,inFlight=false;const tick=async()=>{if(inFlight)return;inFlight=true;try{await refresh();}catch(e){if(alive)setError(e);}finally{inFlight=false;}};void tick();const timer=setInterval(tick,1800);return()=>{alive=false;clearInterval(timer);};},[active,key]);
 const finalRuns=new Set(messages.filter(m=>['qa_result','soc_result','batch_result','translation_result'].includes(m.type)).map(m=>m.run_id));
 const visibleMessages=messages.filter(m=>m.type!=='progress'||!finalRuns.has(m.run_id));
 const latestReplyId=[...visibleMessages].reverse().find(m=>m.role==='assistant')?.id;
 const count=messages.length;
 useEffect(()=>{if(active&&!historyLoaded.current)bottom.current?.scrollIntoView?.({block:'end',behavior:'instant'});},[count]);
 async function olderMessages(){if(!session||!older||loadingOlder)return;setLoadingOlder(true);const scope=key;try{const result=await api(`/sessions/${session.id}/messages?before=${encodeURIComponent(older)}`);if(scope!==context.current)return;historyLoaded.current=true;setMessages(old=>combine(result.items,old));setOlder(result.older_cursor??result.next_cursor??null);}catch(e){if(scope===context.current)setError(e);}finally{setLoadingOlder(false);}}
 async function submit(){
  if(!project||sending.current||(!draft.text.trim()&&!draft.file)||(draft.file&&draft.mode!=='SOC'))return;
  const sent=draft,scope=key,projectId=project.id,requestKey=requestKeys.get(draft)??crypto.randomUUID();requestKeys.set(sent,requestKey);let target=session;
  sending.current=true;setBusy(true);setError(null);
  try{
   target=target??await onNewSession()??null;if(!target)throw new Error('请先创建对话');if(!session){drafts.set(`${projectId}:${target.id}`,sent);if(context.current===scope||context.current===`${projectId}:${target.id}`)setDraftState({key:`${projectId}:${target.id}`,value:sent});}
   if(sent.file){
    const form=new FormData();form.set('file',sent.file);form.set('purpose','bidding');
    const saved=uploaded.current?.file===sent.file?{id:uploaded.current.id}:await api(`/projects/${projectId}/files`,'POST',form);uploaded.current={file:sent.file,id:saved.id};
    await api(`/projects/${projectId}/excel/prechecks`,'POST',{file_id:saved.id,session_id:target.id,product_id:sent.product,task_note:sent.text},requestKey);
   }else if(sent.mode==='TRANSLATION')await api(`/document-tasks/projects/${projectId}/tasks`,'POST',{mode:'TRANSLATION',session_id:target.id,product_id:sent.product,raw_text:sent.text,target_language:sent.targetLanguage??'zh',reference_message_id:sent.referenceMessageId,reference_clause_id:sent.referenceClauseId,confirmed:true},requestKey);
   else await api(`/projects/${projectId}/tasks`,'POST',{mode:sent.mode==='PRODUCT_QA'?'PRODUCT_QA':'SOC_SINGLE',product_id:sent.product,session_id:target.id,raw_text:sent.text},requestKey);
   const cleared={...sent,text:'',file:null,referenceMessageId:undefined,referenceClauseId:undefined};
   drafts.set(scope,cleared);drafts.set(`${projectId}:${target.id}`,cleared);
   if(context.current===scope||context.current===`${projectId}:${target.id}`)setDraftState({key:context.current,value:cleared});
   requestKeys.delete(sent);localKey.current=crypto.randomUUID();uploaded.current=null;await refresh(target.id,`${projectId}:${target.id}`);
  }catch(e){if(context.current===scope||context.current===`${projectId}:${target?.id}`)setError(e);}finally{sending.current=false;setBusy(false);}
 }
 async function projectCreate(e:React.FormEvent){e.preventDefault();if(!products.length){setError(new Error('请先登记产品型号，再创建项目'));return;}try{await api('/projects','POST',{name,default_product_id:draft.product});setName('');await onProjectsChanged();}catch(e){setError(e);}}
 const disabled=busy||(!draft.text.trim()&&!draft.file)||(!!draft.file&&draft.mode!=='SOC');
 return <section className="conversation-workspace" aria-label="对话工作台">
  {!project?<div className="conversation-welcome"><img src="/laura-mark.svg" alt="" width="42" height="42"/><h1>创建项目，开始与 Laura 对话</h1><p>先选择产品资料所属的项目。</p><form className="inline-form" onSubmit={projectCreate}><label>项目名称<input required value={name} onChange={e=>{setError(null);setName(e.target.value);}}/></label><label>默认型号<select value={draft.product} onChange={e=>change({product:e.target.value})}>{products.map(p=><option key={p.id} value={p.id}>{productLabel(p)}</option>)}</select></label><button className="primary" disabled={!products.length}>创建项目</button>{!products.length&&<a href="#libraries/products/new">去登记产品型号</a>}</form></div>:<>
  <div className="conversation-thread" aria-label="对话消息">
   {session?.copied_from&&<div className="session-copy-notice">来自「{session.copied_from.project_name} / {session.copied_from.session_name}」的{session.copied_from.kind==='branch'?'分支':'项目副本'}。历史仅作对话参考；任务、审核与原始来源保留在原项目。</div>}
   {older&&<button className="conversation-earlier" onClick={olderMessages} disabled={loadingOlder}>{loadingOlder?'正在加载…':'加载更早消息'}</button>}
   {!messages.length&&<div className="conversation-welcome"><img src="/laura-mark.svg" alt="" width="40" height="40"/><h1>有什么需要 Laura 帮忙？</h1><p>提问产品能力、应标处理条款，或翻译单条文本。</p></div>}
   {visibleMessages.map(message=><ConversationMessage historical={message.role==='assistant'&&message.id!==latestReplyId} key={message.role==='assistant'&&message.run_id?message.run_id:message.id} active={active} message={message} project={project} products={products} session={session} onNewSession={onNewSession} onTask={onTask} onRefresh={()=>message.run_id?refreshRun(message.run_id):refresh()} onReference={(referenceClauseId?:string)=>referenceMessage(message,referenceClauseId)} onEdit={()=>editMessage(message)} editDisabled={busy}/>) }
   <div ref={bottom}/>
  </div>
  <div className="conversation-composer-dock"><ErrorNotice error={error}/><div className="composer-context-bar" aria-label="项目与产品型号">{onChooseProject&&<ProjectSelector projects={projects} projectId={project.id} onChooseProject={onChooseProject} onCreateProject={onCreateProject??(()=>{})} onManageProjects={onManageProjects??(()=>{})}/>}<Select aria-label="本次任务型号" value={draft.product} disabled={busy} suffixIcon={<DatabaseOutlined aria-hidden="true"/>} options={products.map(p=>({value:p.id,label:productLabel(p)}))} onChange={product=>change({product})}/></div><div className="conversation-composer">
   <Input.TextArea aria-label="输入消息" placeholder={draft.mode==='PRODUCT_QA'?'向 Laura 提问产品问题…':draft.mode==='TRANSLATION'?'粘贴要翻译的文本，或引用上方已完成回答…':'粘贴 SOC 条款，或添加 Excel；附带文字仅作任务备注…'} autoSize={{minRows:3,maxRows:8}} value={draft.text} readOnly={busy} onChange={e=>change({text:e.target.value})} onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&!composing.current&&e.keyCode!==229){e.preventDefault();void submit();}}}/>
   {draft.referenceMessageId&&<div className="conversation-attachment"><span title={draft.referenceMessageId}>已引用会话消息{draft.referenceClauseId?' · 指定条目':''}</span><Button type="text" size="small" disabled={busy} onClick={()=>change({referenceMessageId:undefined,referenceClauseId:undefined,text:''})}>取消引用</Button></div>}
   {draft.file&&<div className="conversation-attachment"><Icon name="table" size={17}/><span>{draft.file.name}</span><button aria-label="移除附件" disabled={busy} onClick={()=>change({file:null})}><Icon name="close" size={14}/></button></div>}
   <div className="conversation-controls"><div className="composer-controls-left"><Button type="text" icon={<PlusOutlined/>} aria-label="选择 Excel 附件" disabled={busy} onClick={()=>fileInput.current?.click()}/><input ref={fileInput} className="sr-only" type="file" accept=".xlsx" aria-label="添加 Excel 附件" disabled={busy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(!/\.xlsx$/i.test(file.name)){setError(new Error('请选择普通 .xlsx 文件'));return;}change({file});}}/></div><div className="composer-controls-right"><ContextUsage mode={draft.mode} active={active} runId={[...messages].reverse().find(m=>m.run_id&&(draft.mode==='TRANSLATION'?m.mode==='TRANSLATION':draft.mode==='PRODUCT_QA'?m.mode==='PRODUCT_QA':['SOC_SINGLE','SOC_BATCH'].includes(m.mode)))?.run_id} sessionId={session?.id} productId={draft.product} rawText={draft.text} referenceMessageId={draft.referenceMessageId} referenceClauseId={draft.referenceClauseId} targetLanguage={draft.targetLanguage??'zh'} busy={busy||messages.some(m=>['queued','running'].includes(m.job?.status))} onNewSession={()=>void onNewSession()}/><ComposerMode mode={draft.mode} targetLanguage={draft.targetLanguage??'zh'} disabled={busy} onModeChange={mode=>change({mode,referenceMessageId:undefined,referenceClauseId:undefined})} onTargetLanguageChange={targetLanguage=>change({targetLanguage})}/><Button type="primary" shape="circle" icon={<ArrowUpOutlined/>} aria-label="发送消息" title="发送消息（Enter）" disabled={disabled} loading={busy} onClick={submit}/></div></div>
  </div><p className="conversation-input-note">{draft.file&&draft.mode!=='SOC'?'附件已保留。请手动切换到“应标”后发送。':draft.file?'附带文字仅保存为任务备注，不参与条款判定。':'Enter 发送 · Shift + Enter 换行；结果仍须核对来源与审核。'}</p></div>
  </>}
 </section>;
}

function ConversationMessage({historical=false,active=true,message:m,project,session,onTask,onNewSession,onRefresh,onReference,onEdit,editDisabled}:{historical?:boolean;onReference:(clauseId?:string)=>void;onEdit:()=>void;editDisabled:boolean;active?:boolean;message:Data;project:Data;products:Data[];session:Data|null;onTask:(id:string,run?:string,clauseId?:string)=>void;onNewSession:()=>Promise<Data|undefined>;onRefresh:()=>Promise<void>}){
 const [error,setError]=useState<unknown>(null),[copied,setCopied]=useState(false);
 useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),1600);return()=>clearTimeout(timer);},[copied]);
 async function copy(){try{await navigator.clipboard.writeText(m.text??'');setCopied(true);}catch{setError(new Error('复制失败，请重试'));}}
 const result=m.role==='assistant'?(m.result??m.payload?.result):null,qa=m.type==='qa_result'||!!result?.answer;

 return <article className={`conversation-message ${m.role==='user'?'from-user':'from-assistant'}${m.type==='excel_confirmation'?' confirmation-message':''}${historical?' historical-reply':''}`} aria-label={m.role==='user'?'用户消息':'Laura 消息'}>
  <div className="conversation-message-label">{m.role==='user'?'你':'Laura'}</div>
  {m.review_feedback?<ReviewFeedbackCard feedback={m.review_feedback} onTask={id=>onTask(id,m.run_id)}/>:m.type==='excel_confirmation'&&m.card?<><p>{m.card.task_note&&<>任务备注：{m.card.task_note}</>}</p><ExcelUpload initialCheck={{...m.card,filename:m.text}} projectId={project.id} productId={m.card.product_id} session={session} onNewSession={onNewSession} onTask={onTask} onConfirmed={onRefresh}/></>:<>
   {m.role==='user'&&m.text&&<p className="conversation-message-text">{m.text}</p>}
   {!result&&m.role==='assistant'&&m.type!=='batch_result'&&m.type!=='progress'&&m.text&&<p className="conversation-message-text">{m.text}</p>}
   {m.run_id&&m.role==='assistant'&&<RunActivity active={active} runId={m.run_id} job={m.job} onRefresh={onRefresh}/>}
   {result&&<ResponseBlock timestamp={m.type==='progress'?undefined:m.created_at} result={result} qa={qa} translation={m.mode==='TRANSLATION'} revised={m.manually_revised} reviewStatus={m.review_status} onReview={m.mode==='SOC_SINGLE'&&m.clause_id?()=>onTask(m.task_id,m.run_id,m.clause_id):undefined} onReference={m.job?.status==='completed'&&m.mode!=='SOC_BATCH'?()=>onReference():undefined}/>}
   {(m.type==='batch_result'||(m.type==='progress'&&m.mode!=='TRANSLATION'&&m.job?.total_items>1))&&m.run_id&&<BatchReply onReview={clause=>onTask(m.task_id,m.run_id,clause)} onReference={onReference} runId={m.run_id} job={m.job} timestamp={m.type==='batch_result'?m.created_at:undefined}/>}


   {m.is_current_run===false&&<p className="source-version">历史运行 · 内容始终绑定原运行。</p>}
  </>}
  {m.role==='user'&&<div className="user-message-actions"><time title={m.created_at}>{m.created_at?new Date(m.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):''}</time><Button type="text" size="small" aria-label="复制消息" icon={copied?<CheckOutlined/>:<CopyOutlined/>} onClick={()=>void copy()}/>{['qa_input','soc_input','translation_input'].includes(m.type)&&<Button type="text" size="small" aria-label="编辑消息" disabled={editDisabled} icon={<EditOutlined/>} onClick={onEdit}/>}</div>}
  <ErrorNotice error={error}/>
 </article>;
}
