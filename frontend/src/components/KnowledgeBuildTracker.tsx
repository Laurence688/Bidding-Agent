import {useEffect,useState} from 'react';
import {Alert,Button} from 'antd';
import {api,Data} from '../api/client';
import {buildTerminalMessage} from './knowledgeModel';
const storageKey='laura-kb-build-tracker-v1';
export const trackKnowledgeBuild=(build:Data,libraryId:string)=>window.dispatchEvent(new CustomEvent('kb-build-submitted',{detail:{...build,id:build.id??build.build_id,library_id:libraryId}}));
export default function KnowledgeBuildTracker({onNavigate}:{onNavigate?:(page:string)=>void}){
 const [notices,setNotices]=useState<Data[]>([]),[offline,setOffline]=useState(false);
 useEffect(()=>{let alive=true,timer:ReturnType<typeof setTimeout>;let tracked:Record<string,Data>={};try{tracked=JSON.parse(localStorage.getItem(storageKey)??'{}');}catch{}let initialized=false;let busy=false;
 const persist=()=>{try{localStorage.setItem(storageKey,JSON.stringify(tracked));}catch{/* 浏览器禁用存储时仍保留当前页跟踪。 */}};
 const add=(event:Event)=>{const b=(event as CustomEvent).detail;if(!b.id)return;tracked[b.id]={...b,status:b.status??'queued',seen_terminal:false};persist();};
 window.addEventListener('kb-build-submitted',add);const published=(event:Event)=>{const id=(event as CustomEvent).detail.id;setNotices(old=>old.map(n=>n.id===id?{...n,text:'构建已发布'}:n));};window.addEventListener('kb-build-published',published);
 async function tick(){if(busy||!alive)return;busy=true;try{if(!initialized){const ls=await api('/kb/libraries');for(const lib of ls.items??[]){if(!alive)return;const detail=await api(`/kb/libraries/${lib.id}`);for(const b of detail.builds??[]){const old=tracked[b.id];if(old)tracked[b.id]={...old,...b,library_id:lib.id,library_name:lib.name,active_snapshot_id:lib.active_snapshot_id};else if(['queued','running','building'].includes(b.status))tracked[b.id]={...b,library_id:lib.id,library_name:lib.name,active_snapshot_id:lib.active_snapshot_id,seen_terminal:false};}}initialized=true;}
 for(const id of Object.keys(tracked)){if(!alive)return;const old=tracked[id];if(old.seen_terminal)continue;const b=await api(`/kb/builds/${id}`);if(!alive)return;tracked[id]={...old,...b};window.dispatchEvent(new CustomEvent('kb-build-updated',{detail:{...tracked[id]}}));const text=buildTerminalMessage(b.status);if(text){tracked[id].seen_terminal=true;setNotices(items=>[...items.filter(n=>n.id!==id),{id,library_id:old.library_id,title:old.library_name??'知识库',text:b.publication_status==='published'||b.published||b.snapshot_id&&b.snapshot_id===old.active_snapshot_id?'构建已发布':text,status:b.status}]);}persist();}if(alive)setOffline(false);
 }catch{if(alive&&Object.values(tracked).some(b=>!b.seen_terminal))setOffline(true);/* 网络恢复后继续追踪，避免将离线误报为构建失败。 */}finally{busy=false;if(alive)timer=setTimeout(tick,1800);}}
 void tick();return()=>{alive=false;clearTimeout(timer);window.removeEventListener('kb-build-submitted',add);window.removeEventListener('kb-build-published',published);};},[]);
 return <aside className="kb-global-notices" aria-label="知识库构建通知" aria-live="polite">{offline&&<Alert type="warning" title="构建状态连接暂时中断，正在重连；已保留最后进度。"/>}{notices.map(n=><Alert key={n.id} showIcon type={['ready','completed','succeeded'].includes(n.status)?'success':n.status==='failed'?'error':'warning'} title={`${n.title} · ${n.text}`} action={<><Button size="small" type="link" onClick={()=>{if(onNavigate)onNavigate("libraries/"+encodeURIComponent(n.library_id));else location.hash="libraries/"+encodeURIComponent(n.library_id);}}>查看构建</Button><Button size="small" type="text" onClick={()=>setNotices(old=>old.filter(x=>x.id!==n.id))}>关闭</Button></>}/>)}</aside>;
}
