import ProjectCreateDialog from './components/ProjectCreateDialog';
import ProductEditor from './components/ProductEditor';
import ConversationEntry from './components/ConversationEntry';
import SidebarResizeHandle from './components/SidebarResizeHandle';
import {useSidebarWidth} from './hooks/useSidebarWidth';
import type {CSSProperties} from 'react';
import SessionActionDialog from './components/SessionActionDialog';
import KnowledgeBuildTracker from './components/KnowledgeBuildTracker';
import {useEffect,useRef,useState} from 'react';
import {api,bootstrap,Data,displaySessionName} from './api/client';
import {ErrorNotice} from './components/Status';
import {Icon,IconName} from './components/Icon';
import BackToTop from './components/BackToTop';
import {usePageNavigation} from './hooks/usePageNavigation';
import Workspace from './pages/Workspace';
import TaskDetail from './pages/TaskDetail';
import Knowledge from './pages/Knowledge';
import Evaluation from './pages/Evaluation';
import Settings from './pages/Settings';
import Observability from './pages/Observability';
import Prompts from './pages/Prompts';
import Documents from './pages/Documents';
import './layout.css';
import './pages/product.css';
import Home from './pages/Home';
import Tools from './pages/Tools';
import Libraries from './pages/Libraries';
import Memories from './pages/Memories';
import EmbeddingSettings from './pages/EmbeddingSettings';
import RuntimeSettings,{SystemNavigation} from './pages/SystemSettings';
import Projects from './pages/Projects';
import ProjectSelector from './components/ProjectSelector';
import BeijingClock from './components/BeijingClock';
import ProductNavigation from './components/ProductNavigation';
import ReviewWorkbench from './pages/ReviewWorkbench';
import ExecutionLogs from './pages/ExecutionLogs';
import DocumentTasks from './pages/DocumentTasks';

const navigation:{key:string;icon:IconName;title:string}[]=[
 {key:'workspace',icon:'grid',title:'工作台'},
 {key:'observability',icon:'activity',title:'观测'},
 {key:'knowledge',icon:'book',title:'知识来源'},
 {key:'evaluation',icon:'flask',title:'评测'},
 {key:'docs',icon:'book',title:'文档'},
 {key:'prompts',icon:'prompt',title:'Prompt'},
 {key:'settings',icon:'settings',title:'模型设置'},
];

export default function App(){
 const [ready,setReady]=useState(false),[products,setProducts]=useState<Data[]>([]),[projects,setProjects]=useState<Data[]>([]),[project,setProject]=useState<Data|null>(null),[sessions,setSessions]=useState<Data[]>([]),[session,setSession]=useState<Data|null>(null),[error,setError]=useState<any>(null),[health,setHealth]=useState('');
 const {page,navigate:changePage,back,canGoBack}=usePageNavigation(project?.id??null);
 const sidebarWidth=useSidebarWidth();
 const [sessionOperation,setSessionOperation]=useState<string|null>(null),[copyNotice,setCopyNotice]=useState<Data|null>(null);
 const operationPending=useRef(false),copyKeys=useRef(new Map<string,string>());
 const [collapsed,setCollapsed]=useState(()=>localStorage.getItem('soc-sidebar-collapsed')==='true');
 const [mobile,setMobile]=useState(()=>window.matchMedia('(max-width: 700px)').matches),[mobileOpen,setMobileOpen]=useState(false);
 const [workspaceVisits,setWorkspaceVisits]=useState<{key:string;project:Data;session:Data|null}[]>([]);
 const [documentVisits,setDocumentVisits]=useState<{key:string;project:Data;mode:'TRANSLATION'|'TENDER_ANALYSIS'}[]>([]);
 const [creatingProject,setCreatingProject]=useState(false),[projectName,setProjectName]=useState(''),[projectProduct,setProjectProduct]=useState('');
 const productCatalogEpoch=useRef(0);
 const refreshProductCatalog=async()=>{const epoch=++productCatalogEpoch.current,value=await bootstrap();if(epoch===productCatalogEpoch.current)setProducts(value.products);return value;};
 const sessionAliases=useRef<Record<string,string>>({});
 const sessionCache=useRef<Record<string,Data|null>>({});
 const [sessionDialog,setSessionDialog]=useState<{session:Data;remove:boolean}|null>(null);
 const [sessionSearch,setSessionSearch]=useState(''),[sessionBusy,setSessionBusy]=useState(false);
 const registerReturn=useRef<string|null>(null);
 const registerProduct=()=>{registerReturn.current=page;setCreatingProject(false);navigate('libraries/products/new');};
 const drawer=useRef<HTMLDialogElement>(null),drawerClose=useRef<HTMLButtonElement>(null),creatingSession=useRef(false);
 const selectedProjectId=useRef(project?.id);selectedProjectId.current=project?.id;
 const navigationEpoch=useRef(0);
 const activeDocumentKey=useRef('');activeDocumentKey.current=project&&['translation','tender-analysis'].includes(page.split('?')[0])?`${project.id}:${page.startsWith('translation')?'TRANSLATION':'TENDER_ANALYSIS'}`:'';
 useEffect(()=>{const moved=()=>{navigationEpoch.current++;};window.addEventListener('popstate',moved);window.addEventListener('hashchange',moved);return()=>{window.removeEventListener('popstate',moved);window.removeEventListener('hashchange',moved);};},[]);
 const selectedSessionId=useRef(session?.id);selectedSessionId.current=session?.id;
 const navigate=(value:string,reset=false)=>{navigationEpoch.current++;changePage(value,{reset});setMobileOpen(false);};
 const loadSessions=async(id:string)=>{
  const items:Data[]=[];let cursor:string|null=null;
  do{const result=await api(`/projects/${id}/sessions${cursor?'?cursor='+encodeURIComponent(cursor):''}`);items.push(...result.items);cursor=result.next_cursor??null;}while(cursor);
  return {items};
 };
 const refreshProjects=async()=>{const all:Data[]=[];let cursor:string|null=null;do{const result=await api('/projects'+(cursor?'?cursor='+encodeURIComponent(cursor):''));all.push(...result.items);cursor=result.next_cursor??null;}while(cursor);const list=all.filter((p:Data)=>!p.evaluation_only);setProjects(list);const activeIds=new Set(list.map(p=>p.id));setWorkspaceVisits(old=>old.filter(v=>activeIds.has(v.project.id)));setDocumentVisits(old=>old.filter(v=>activeIds.has(v.project.id)));for(const id of Object.keys(sessionCache.current))if(!activeIds.has(id)){delete sessionCache.current[id];sessionStorage.removeItem('laura-session:'+id);}setProject(current=>list.find((p:Data)=>p.id===(current?.id??localStorage.getItem('soc-project')))||list[0]||null);};

 useEffect(()=>{refreshProductCatalog().then(()=>refreshProjects()).then(()=>setReady(true)).catch(setError);},[]);
 useEffect(()=>{setMobileOpen(false);},[page]);
 useEffect(()=>{
  setSessions([]);setSession(null);setSessionSearch('');
  if(!project){localStorage.removeItem('soc-project');return;}
  let active=true;localStorage.setItem('soc-project',project.id);
  const remembered=sessionCache.current[project.id]?.id??sessionStorage.getItem('laura-session:'+project.id);
  loadSessions(project.id).then(data=>{if(active){setSessions(data.items);setSession(data.items.find((s:Data)=>s.id===remembered)||data.items[0]||null);}}).catch(e=>{if(active)setError(e);});
  return()=>{active=false;};
 },[project?.id]);
 useEffect(()=>{if(!ready)return;const tick=()=>api('/health').then(v=>setHealth(v.worker)).catch(()=>setHealth('disconnected'));tick();const id=setInterval(tick,5000);return()=>clearInterval(id);},[ready]);
 useEffect(()=>{localStorage.setItem('soc-sidebar-collapsed',String(collapsed));},[collapsed]);
 useEffect(()=>{const media=window.matchMedia('(max-width: 700px)');const update=()=>{setMobile(media.matches);setMobileOpen(false);};media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 useEffect(()=>{
  if(!mobile||!drawer.current)return;
  if(mobileOpen){drawer.current.showModal();drawerClose.current?.focus();const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{drawer.current?.close();document.body.style.overflow=previous;};}
  drawer.current.close();
 },[mobile,mobileOpen]);

 const newSession=async(preserveDraft=false)=>{
  if(!project||creatingSession.current)return;
  const startedEpoch=navigationEpoch.current,startedSession=selectedSessionId.current;
  creatingSession.current=true;setSessionBusy(true);
  try{const created=await api(`/projects/${project.id}/sessions`,'POST',{});if(preserveDraft)sessionAliases.current[created.id]=workspaceKey;if(selectedProjectId.current===project.id){setSessions(old=>[created,...old]);if(selectedSessionId.current===startedSession){setSession(created);if(navigationEpoch.current===startedEpoch)navigate('workspace');}}return created;}catch(e){if(selectedProjectId.current===project.id)setError(e);}finally{creatingSession.current=false;setSessionBusy(false);}

 };
 const workspaceKey=project?sessionAliases.current[session?.id??'']??`${project.id}:${session?.id??'draft'}`:'empty';
 useEffect(()=>{if(project)setWorkspaceVisits(old=>old.some(v=>v.key===workspaceKey)?old.map(v=>v.key===workspaceKey?{...v,project,session}:v):[...old,{key:workspaceKey,project,session}]);},[workspaceKey,session?.id,project]);
 useEffect(()=>{if(project&&(!session||session.project_id===project.id)){sessionCache.current[project.id]=session;if(session)sessionStorage.setItem('laura-session:'+project.id,session.id);}},[project?.id,session]);
 useEffect(()=>{if(!project||!['translation','tender-analysis'].includes(page.split('?')[0]))return;const mode=page.startsWith('translation')?'TRANSLATION':'TENDER_ANALYSIS',key=`${project.id}:${mode}`;setDocumentVisits(old=>old.some(v=>v.key===key)?old:[...old,{key,project,mode}]);},[project?.id,page]);
 useEffect(()=>{const refresh=()=>refreshProductCatalog().catch(()=>{});window.addEventListener('focus',refresh);const visible=()=>{if(!document.hidden)void refresh();};document.addEventListener('visibilitychange',visible);return()=>{window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',visible);};},[]);
 const workArea=page==='workspace'||page.startsWith('task/');
 const activeNav=page.startsWith('task/')?'workspace':page.startsWith('docs')?'docs':['prompts','settings'].includes(page)?'config':page.split('/')[0];
 const sortedSessions=sessions.slice().sort((a,b)=>Number(!!b.pinned)-Number(!!a.pinned)||String(b.created_at).localeCompare(String(a.created_at)));
 const visibleSessions=sortedSessions.filter(s=>displaySessionName(s).toLowerCase().includes(sessionSearch.toLowerCase()));
 const healthLabel=health==='running'?'后台任务已连接':health==='disconnected'?'服务连接中断':'后台任务未启动';
 const chooseProject=(id:string)=>{navigationEpoch.current++;changePage('workspace',{reset:true,onCommit:()=>{setProject(projects.find(p=>p.id===id)||null);setSession(sessionCache.current[id]??null);setMobileOpen(false);}});};
 function openSessionDialog(s:Data,remove=false){setMobileOpen(false);setSessionDialog({session:s,remove});}
 async function sessionAction(s:Data,remove=false,name?:string){
  const owner=s.project_id;
  // 弹窗始终绑定打开时的会话，异步响应不得覆盖后来切换的项目。
  if(remove){
   await api(`/sessions/${s.id}`,'DELETE',{expected_version:s.version,cancel_running:true});
   setWorkspaceVisits(old=>old.filter(v=>v.session?.id!==s.id));
   if(selectedProjectId.current===owner){setSession(current=>current?.id===s.id?null:current);setSessions(old=>old.filter(v=>v.id!==s.id));}
  }else{
   const changed=await api(`/sessions/${s.id}`,'PATCH',{name,expected_version:s.version});
   if(selectedProjectId.current===owner){setSession(current=>current?.id===s.id?changed:current);setSessions(old=>old.map(v=>v.id===s.id?changed:v));}
  }
  if(selectedProjectId.current===owner){try{const list=await loadSessions(owner);if(selectedProjectId.current===owner)setSessions(list.items);}catch(e){if(selectedProjectId.current===owner)setError(e);}}
 }
 function enterCopiedSession(created:Data){
  const owner=projects.find(p=>p.id===created.project_id);if(!owner)return;
  navigationEpoch.current++;
  changePage('workspace',{reset:true,onCommit:()=>{
   sessionCache.current[owner.id]=created;sessionStorage.setItem('laura-session:'+owner.id,created.id);
   setProject(owner);setSession(created);setMobileOpen(false);setCopyNotice(null);
   if(selectedProjectId.current===owner.id)setSessions(old=>old.some(v=>v.id===created.id)?old:[created,...old]);
  }});
 }
 async function organizeSession(s:Data,targetProjectId?:string){
  if(operationPending.current)return;
  operationPending.current=true;setSessionOperation(s.id);setError(null);
  const startedEpoch=navigationEpoch.current,startedProject=selectedProjectId.current,startedSession=selectedSessionId.current;
  try{
   if(!targetProjectId){
    const changed=await api(`/sessions/${s.id}`,'PATCH',{expected_version:s.version,pinned:!s.pinned});
    if(selectedProjectId.current===s.project_id){setSessions(old=>old.map(v=>v.id===s.id?changed:v));setSession(current=>current?.id===s.id?changed:current);}
   }else{
    const key=`${s.id}:${s.version}:${targetProjectId}`;if(!copyKeys.current.has(key))copyKeys.current.set(key,crypto.randomUUID());
    const created=await api(`/sessions/${s.id}/copies`,'POST',{expected_version:s.version,target_project_id:targetProjectId},copyKeys.current.get(key));
    copyKeys.current.delete(key);setCopyNotice(created);
    if(navigationEpoch.current===startedEpoch&&selectedProjectId.current===startedProject&&selectedSessionId.current===startedSession)enterCopiedSession(created);
   }
  }catch(e){setError(e);}finally{operationPending.current=false;setSessionOperation(null);}
 }
 const sidebarContent=<>
  {mobile&&<button ref={drawerClose} className="icon-button" aria-label="关闭导航" onClick={()=>setMobileOpen(false)}><Icon name="close"/></button>}
  <button className="new-session-button" aria-label="新建对话" disabled={!project||sessionBusy} onClick={()=>newSession()}><Icon name="plus" size={18}/>{sessionBusy?'正在创建…':'新建对话'}</button>
  <label className="session-search"><Icon name="search" size={15}/><input aria-label="搜索对话" placeholder="搜索对话" value={sessionSearch} onChange={e=>setSessionSearch(e.target.value)}/></label>
  <section className="session-section" aria-label="项目对话"><div className="session-heading">对话 <small>{sessions.length}</small></div><div className="session-list">{visibleSessions.map(s=><ConversationEntry key={s.id} session={s} projects={projects} selected={session?.id===s.id} busy={sessionOperation!==null} onSelect={()=>{setSession(s);navigate('workspace');}} onRename={()=>openSessionDialog(s)} onDelete={()=>openSessionDialog(s,true)} onPin={()=>void organizeSession(s)} onCopy={id=>void organizeSession(s,id)}/>)}{!visibleSessions.length&&<p>{sessions.length?'未找到匹配的对话':'新建对话，开始整理投标要求。'}</p>}</div></section>
  {!mobile&&<button className="icon-button sidebar-collapse-bottom" aria-label="收起侧栏" onClick={()=>setCollapsed(true)}><Icon name="panel-left"/></button>}
 </>;

 return <div style={{'--conversation-sidebar-width':`${sidebarWidth.width}px`} as CSSProperties} className={`laura-layout conversation-layout${workArea&&!collapsed?' has-conversations':''}${page.startsWith('docs')?' document-layout':''}`}>
  <a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById('main-content')?.focus();}}>跳到主要内容</a>
  {<header className="module-header">

   <a className="brand" href="#home" aria-label="Laura 首页" onClick={e=>{e.preventDefault();navigate('home');}}><img src="/laura-mark.svg" width="32" height="32" alt=""/><span><b>Laura</b></span></a>

   <ProductNavigation page={page} onNavigate={navigate}/>

   <ProjectSelector projects={projects} projectId={project?.id} onChooseProject={chooseProject} onCreateProject={()=>setCreatingProject(true)} onManageProjects={()=>navigate('projects')}/>
   <BeijingClock/>
  </header>}
  {workArea&&(mobile?<dialog ref={drawer} className="sidebar-drawer" aria-label="对话列表" onClose={()=>setMobileOpen(false)}><aside className="conversation-sidebar">{sidebarContent}</aside></dialog>:!collapsed&&<aside className="conversation-sidebar">{sidebarContent}<SidebarResizeHandle width={sidebarWidth.width} limit={sidebarWidth.limit} onChange={sidebarWidth.setWidth} onReset={sidebarWidth.reset}/></aside>)}
  {workArea&&(collapsed||mobile)&&<button className="icon-button sidebar-restore" aria-label={mobile?'打开对话':'展开侧栏'} onClick={()=>mobile?setMobileOpen(true):setCollapsed(false)}><Icon name="panel-left"/></button>}
  {sessionDialog&&<SessionActionDialog key={`${sessionDialog.session.id}:${sessionDialog.remove}`} session={sessionDialog.session} remove={sessionDialog.remove} onClose={()=>setSessionDialog(null)} onSubmit={name=>sessionAction(sessionDialog.session,sessionDialog.remove,name)}/>}
  <main id="main-content" className="layout-content" tabIndex={-1}>
   {copyNotice&&<div className="session-copy-notice" role="status">已创建对话「{copyNotice.name}」。<button onClick={()=>enterCopiedSession(copyNotice)}>打开对话</button></div>}

   {canGoBack&&!page.startsWith('libraries/')&&<button className="layout-back" onClick={back}><Icon name="arrow-left" size={15}/>返回上一级</button>}<ErrorNotice error={error} onRefresh={()=>location.reload()}/>{ready&&<KnowledgeBuildTracker onNavigate={navigate}/>}
   {['settings','embedding','prompts','runtime'].includes(page)&&<SystemNavigation page={page} onNavigate={navigate}/>}
   {page==='observability'&&<p className="scope-caption">作用范围：当前项目</p>}
   {!ready?<div className="empty">正在连接本机服务…</div>:<>
    {(project?workspaceVisits:[]).map(v=><div key={v.key} hidden={page!=='workspace'||v.key!==workspaceKey}><Workspace projects={projects} onChooseProject={chooseProject} onCreateProject={()=>setCreatingProject(true)} onManageProjects={()=>navigate('projects')} active={page==='workspace'&&v.key===workspaceKey} project={v.project} products={products} session={v.key===workspaceKey?session:v.session} onProjectsChanged={refreshProjects} onTask={(id,runId,clauseId)=>navigate(`review?task=${encodeURIComponent(id)}${runId?'&run='+encodeURIComponent(runId):''}${clauseId?'&clause='+encodeURIComponent(clauseId):''}`)} onNewSession={()=>newSession(true)} onSessionChanged={s=>{setSession(s);if(project)loadSessions(project.id).then(v=>setSessions(v.items)).catch(setError);}}/></div>)}
    {!project&&page==='workspace'&&<div className="workspace-onboarding"><h1>欢迎使用 Laura</h1><p>创建项目，开始产品问答和投标应答。</p><button className="primary" onClick={()=>setCreatingProject(true)}>创建项目</button></div>}

    {documentVisits.map(v=>{const active=v.project.id===project?.id&&((v.mode==='TRANSLATION'&&page.split('?')[0]==='translation')||(v.mode==='TENDER_ANALYSIS'&&page.split('?')[0]==='tender-analysis'));const epoch=navigationEpoch.current;return <div key={v.key} hidden={!active}><DocumentTasks project={v.project} mode={v.mode} active={active} initialRunId={active?new URLSearchParams(page.split('?')[1]).get('run')??undefined:undefined} onRun={id=>{if(active&&selectedProjectId.current===v.project.id&&navigationEpoch.current===epoch&&activeDocumentKey.current===v.key)navigate(`${v.mode==='TRANSLATION'?'translation':'tender-analysis'}?run=${id}`);}}/></div>;})}
    {page.startsWith('review')?<ReviewWorkbench project={project} products={products} route={page} onNavigate={navigate}/>:page.startsWith('logs')?<ExecutionLogs project={project} route={page} onNavigate={navigate}/>:page==='home'?<Home onNavigate={navigate}/>:page==='tools'?<Tools/>:page.startsWith('projects')?<Projects projects={projects} products={products} route={page} onNavigate={navigate} onChooseProject={chooseProject} onCreateProject={()=>setCreatingProject(true)} onProjectsChanged={refreshProjects}/>:page==='libraries/products/new'?<ProductEditor open onClose={()=>{const target=registerReturn.current;registerReturn.current=null;navigate(target??'libraries');if(target)setCreatingProject(true);}} onSaved={async p=>{await refreshProductCatalog();setProjectProduct(p.id);}}/>:page.startsWith('libraries')?<Libraries route={page} onNavigate={navigate} onBack={back} onProjectsChanged={refreshProjects} project={project} products={products} onProductsChanged={async()=>{await refreshProductCatalog();}}/>:page==='memories'?<Memories products={products} project={project} onTask={(id,runId)=>navigate(`task/${id}${runId?'?run='+runId:''}`)}/>:page==='embedding'?<EmbeddingSettings/>:page==='runtime'?<RuntimeSettings/>:page.startsWith('task/')?<TaskDetail taskId={page.slice(5).split('?')[0]} runId={new URLSearchParams(page.split('?')[1]).get('run')??undefined} products={products} onNavigate={navigate}/>:page.startsWith('observability')?<Observability key={project?.id} project={project} initialTaskId={page.split('/')[1]} onTask={(id,mode,runId)=>navigate(mode==='TRANSLATION'?`translation${runId?'?run='+runId:''}`:mode==='TENDER_ANALYSIS'?`tender-analysis${runId?'?run='+runId:''}`:`task/${id}${runId?'?run='+runId:''}`)}/>:page==='knowledge'?<Knowledge products={products}/>:page==='evaluation'?<Evaluation products={products}/>:page.startsWith('docs')?<Documents route={page} onNavigate={navigate} onBack={canGoBack?back:undefined}/>:page==='prompts'?<Prompts/>:page==='settings'?<Settings/>:null}
   </>}
  </main>{page!=='workspace'&&!page.startsWith('docs')&&<BackToTop/>}
  <span className="connection-status" title={healthLabel} role="status"><span className={`dot ${health==='running'?'online':''}`}/>{healthLabel}</span>
  {creatingProject&&<ProjectCreateDialog products={products} name={projectName} product={projectProduct} onName={setProjectName} onProduct={setProjectProduct} onClose={()=>setCreatingProject(false)} onRegister={registerProduct} onSubmit={async()=>{const created=await api('/projects','POST',{name:projectName.trim(),default_product_id:projectProduct||products[0]?.id||''});await refreshProjects();navigationEpoch.current++;changePage('workspace',{reset:true,onCommit:()=>{setProject(created);setSession(null);setCreatingProject(false);setProjectName('');setMobileOpen(false);}});}}/>}

 </div>;
}
