import {useEffect,useState} from 'react';
import {api,ApiError} from '../api/client';
import {Icon} from '../components/Icon';
import {ErrorNotice} from '../components/Status';
import './prompts.css';

const PROMPT_IDS=['translate','split_claims','judge_claim','verify_semantic_support','render_response','product_qa','skill'] as const;
type PromptId=typeof PROMPT_IDS[number];
type PromptBodies=Record<PromptId,string>;
type PromptItem={id:PromptId;title:string;description:string;content:string;default_content:string;variables:string[];is_default:boolean};
type PromptConfiguration={version:number;content_hash:string;updated_at:string|null;items:PromptItem[]};
type StoredDraft={base_version:number;base_prompts:PromptBodies;prompts:PromptBodies;selected_id:PromptId};
type Editor={config:PromptConfiguration;draft:StoredDraft;conflict:boolean};
const STORAGE_KEY='laura.prompt-drafts.v1';
const MAX_LENGTH=20000;

function isRecord(value:unknown):value is Record<string,unknown>{
 return typeof value==='object'&&value!==null&&!Array.isArray(value);
}
function isBodies(value:unknown):value is PromptBodies{
 return isRecord(value)&&Object.keys(value).length===PROMPT_IDS.length&&PROMPT_IDS.every(id=>Object.hasOwn(value,id)&&typeof value[id]==='string');
}
function readDraft():StoredDraft|null{
 try{
  const raw=sessionStorage.getItem(STORAGE_KEY);if(!raw)return null;
  const value:unknown=JSON.parse(raw);
  if(!isRecord(value)||Object.keys(value).length!==4||!Number.isSafeInteger(value.base_version)||Number(value.base_version)<0||!isBodies(value.base_prompts)||!isBodies(value.prompts)||!PROMPT_IDS.includes(value.selected_id as PromptId))return null;
  return value as StoredDraft;
 }catch{return null;}
}
function readConfiguration(value:unknown):PromptConfiguration{
 if(!isRecord(value)||!Number.isSafeInteger(value.version)||Number(value.version)<0||typeof value.content_hash!=='string'||!(value.updated_at===null||typeof value.updated_at==='string')||!Array.isArray(value.items)||value.items.length!==PROMPT_IDS.length)throw new Error('Prompt 数据不完整，请重新读取。');
 const itemIds=new Set();
 for(const item of value.items){
  if(!isRecord(item)||!PROMPT_IDS.includes(item.id as PromptId)||itemIds.has(item.id)||typeof item.title!=='string'||typeof item.description!=='string'||typeof item.content!=='string'||typeof item.default_content!=='string'||typeof item.is_default!=='boolean'||!Array.isArray(item.variables)||!item.variables.every(variable=>typeof variable==='string'))throw new Error('Prompt 数据不完整，请重新读取。');
  itemIds.add(item.id);
 }
 return value as PromptConfiguration;
}
function editorFrom(config:PromptConfiguration,saved:StoredDraft|null=null,selected:PromptId='translate'):Editor{
 const prompts=Object.fromEntries(config.items.map(item=>[item.id,item.content])) as PromptBodies;
 // A save may finish after this page unmounts. Only an exact match of all
 // seven bodies can make the cached draft clean without discarding edits.
 const restored=saved&&PROMPT_IDS.some(id=>saved.prompts[id]!==prompts[id])?saved:null;
 return {config,draft:restored??{base_version:config.version,base_prompts:prompts,prompts,selected_id:saved?.selected_id??selected},conflict:restored!==null&&restored.base_version!==config.version};
}
function contentIssue(content:string){
 if(!content.trim())return '内容不能为空';
 if(Array.from(content).length>MAX_LENGTH)return '内容超过 20,000 字符';
 return '';
}

export default function Prompts(){
 const [editor,setEditor]=useState<Editor|null>(null);
 const [loading,setLoading]=useState(true),[saving,setSaving]=useState(false);
 const [error,setError]=useState<unknown>(null),[message,setMessage]=useState('');
 const [storageFailed,setStorageFailed]=useState(false);
 const dirtyIds=editor?PROMPT_IDS.filter(id=>editor.draft.prompts[id]!==editor.draft.base_prompts[id]):[];
 const invalidItems=editor?editor.config.items.filter(item=>contentIssue(editor.draft.prompts[item.id])):[];
 const dirtyCount=dirtyIds.length;

 useEffect(()=>{
  let active=true;
  api('/settings/prompts').then(value=>{
   const config=readConfiguration(value);if(!active)return;
   const saved=readDraft(),next=editorFrom(config,saved);setEditor(next);
   if(saved&&next.draft===saved)setMessage('已恢复本标签页中尚未保存的草稿。');
  }).catch(value=>{if(active)setError(value);}).finally(()=>{if(active)setLoading(false);});
  return ()=>{active=false;};
 },[]);

 useEffect(()=>{
  if(!editor)return;
  try{
   if(dirtyCount)sessionStorage.setItem(STORAGE_KEY,JSON.stringify(editor.draft));
   else sessionStorage.removeItem(STORAGE_KEY);
   setStorageFailed(false);
  }catch{setStorageFailed(true);}
 },[editor,dirtyCount]);

 useEffect(()=>{
  if(!dirtyCount)return;
  const preventLoss=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  window.addEventListener('beforeunload',preventLoss);
  return ()=>window.removeEventListener('beforeunload',preventLoss);
 },[dirtyCount]);

 function changeContent(content:string){
  setEditor(current=>current?{...current,draft:{...current.draft,prompts:{...current.draft.prompts,[current.draft.selected_id]:content}}}:current);
  setMessage('');
 }
 async function reload(){
  if(loading||saving)return;
  if(dirtyCount&&!window.confirm('读取最新版本会放弃全部未保存的 Prompt 草稿。确定继续吗？'))return;
  setLoading(true);setError(null);setMessage('');
  try{
   const config=readConfiguration(await api('/settings/prompts'));
   setEditor(current=>editorFrom(config,null,current?.draft.selected_id));
   setMessage('已读取最新版本。');
  }catch(value){setError(value);}finally{setLoading(false);}
 }
 async function save(){
  if(!editor||!dirtyCount||invalidItems.length||editor.conflict||saving||loading)return;
  setSaving(true);setError(null);setMessage('');
  try{
   const config=readConfiguration(await api('/settings/prompts','PUT',{expected_version:editor.draft.base_version,prompts:editor.draft.prompts}));
   setEditor(current=>editorFrom(config,null,current?.draft.selected_id));
   setMessage('Prompt 已保存；新任务或重新运行时生效。');
  }catch(value){
   if(value instanceof ApiError&&value.status===409)setEditor(current=>current?{...current,conflict:true}:current);
   setError(value);
  }finally{setSaving(false);}
 }

 const current=editor?.config.items.find(item=>item.id===editor.draft.selected_id);
 const content=current&&editor?editor.draft.prompts[current.id]:'';
 const currentIssue=contentIssue(content);
 const charCount=Array.from(content).length;
 const updatedAt=editor?.config.updated_at?new Date(editor.config.updated_at):null;
 return <div className="prompt-page"><div role="note" className="notice">建议保留默认提示词。修改可能影响条款拆分、证据判定与最终应答效果，非专业人士请勿修改。</div>
  <div className="page-heading">
   <div><p className="eyebrow">工作流配置</p><h1>Prompt 管理</h1><p>调整各节点的工作要求与表达方式。</p></div>
   <button type="button" className="prompt-reload" disabled={loading||saving} onClick={reload}><Icon name="refresh" size={15}/>{loading?'正在读取…':'读取最新版本'}</button>
  </div>
  <div className="prompt-scope"><Icon name="shield" size={17}/><p>所有项目共用本页配置。保存后用于新任务或重新运行，历史与进行中的任务保持原版本。输出结构、证据白名单与导出校验规则由系统管理。</p></div>
  <ErrorNotice error={error}/>
  {editor?.conflict&&<div className="notice danger prompt-conflict" role="alert"><strong>其他位置已更新 Prompt，当前草稿已保留。</strong><p>请先复制需要保留的内容，再点击“读取最新版本”。确认放弃草稿后，才能基于最新版本继续编辑。</p></div>}
  {message&&<p className="notice success" role="status">{message}</p>}
  {storageFailed&&dirtyCount>0&&<p className="notice danger" role="alert">浏览器未能保留草稿，请在离开页面前保存。</p>}
  {!editor&&<div className="prompt-loading" role="status">{loading?'正在读取 Prompt…':'暂时无法读取 Prompt，请点击“读取最新版本”重试。'}</div>}
  {editor&&current&&<>
   <div className="prompt-workspace" aria-busy={loading||saving}>
    <aside className="prompt-nodes" aria-label="Prompt 节点">
     <div className="prompt-list-heading"><Icon name="book" size={16}/><span>工作节点</span><small>{editor.config.items.length} 项</small></div>
     <div className="prompt-node-list">{editor.config.items.map(item=>{
      const dirty=dirtyIds.includes(item.id),invalid=!!contentIssue(editor.draft.prompts[item.id]);
      return <button type="button" key={item.id} aria-label={item.title} aria-pressed={item.id===current.id} className={`prompt-node${item.id===current.id?' active':''}`} onClick={()=>setEditor(value=>value?{...value,draft:{...value.draft,selected_id:item.id}}:value)}>
       <span className="prompt-node-copy"><strong>{item.title}</strong><small>{item.id}</small></span>
       {invalid?<span className="prompt-node-state invalid" title="内容需要修正" aria-label="内容需要修正">!</span>:dirty?<span className="prompt-node-state dirty" title="未保存" aria-label="未保存"/>:<Icon name="chevron-right" size={14}/>}
      </button>;
     })}</div>
     <p className="prompt-list-note">切换节点时保留草稿<br/>保存会更新全部改动</p>
    </aside>
    <section className="prompt-editor" aria-labelledby="prompt-editor-title">
     <header className="prompt-editor-heading">
      <div><div className="prompt-title-row"><h2 id="prompt-editor-title">{current.title}</h2><span className={`prompt-tag${dirtyIds.includes(current.id)?' changed':''}`}>{dirtyIds.includes(current.id)?'未保存':content===current.default_content?'默认内容':'已保存'}</span></div><p>{current.description}</p></div>
      <button type="button" className="prompt-reset" onClick={()=>{changeContent(current.default_content);setMessage('当前节点已恢复默认草稿，点击“保存 Prompt”后生效。');}} disabled={loading||saving||content===current.default_content}><Icon name="refresh" size={14}/>恢复默认</button>
     </header>
     <details className="prompt-variables" key={current.id}><summary>输入字段说明 <span>{current.variables.length} 项</span></summary><p>以下字段由系统提供，无需在正文中填写具体值。</p><div>{current.variables.map(variable=><code key={variable}>{variable}</code>)}</div></details>
     <div className="prompt-content-label"><label htmlFor="prompt-content"><Icon name="edit" size={14}/>Prompt 内容</label><span id="prompt-char-count" className={charCount>MAX_LENGTH?'invalid':''}>{charCount.toLocaleString()} / 20,000 字符</span></div>
     <textarea id="prompt-content" aria-label="Prompt 内容" aria-describedby={`prompt-char-count${currentIssue?' prompt-validation':''}`} aria-invalid={!!currentIssue} spellCheck={false} value={content} readOnly={loading||saving} onChange={event=>changeContent(event.target.value)}/>
     {currentIssue&&<p className="prompt-validation" id="prompt-validation">{currentIssue}，请修改后再保存。</p>}
     <p className="prompt-editor-note">在此编辑节点正文。保存配置不会发起模型调用。</p>
    </section>
   </div>
   <div className="prompt-savebar">
    <div className="prompt-save-state" aria-live="polite"><strong className={dirtyCount?'has-changes':''}>{dirtyCount?<><span className="prompt-dirty-dot"/>{dirtyCount} 项未保存</>:<><Icon name="check" size={16}/>所有改动已保存</>}</strong><span>{dirtyCount?(storageFailed?'请在离开前保存':'草稿暂存在当前浏览器标签页'):<>当前版本 v{editor.draft.base_version}{updatedAt&&!Number.isNaN(updatedAt.getTime())?` · ${updatedAt.toLocaleString('zh-CN',{hour12:false})}`:''}</>}</span></div>
    <button type="button" className="primary prompt-save" onClick={save} disabled={!dirtyCount||!!invalidItems.length||editor.conflict||saving||loading}><Icon name="check" size={16}/>{saving?'正在保存…':'保存 Prompt'}</button>
   </div>
   {invalidItems.length>0&&<p className="prompt-validation prompt-all-validation" role="status">请先修正：{invalidItems.map(item=>`${item.title}（${contentIssue(editor.draft.prompts[item.id])}）`).join('、')}。</p>}
  </>}
 </div>;
}
