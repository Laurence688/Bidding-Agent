import {useEffect,useRef,useState} from 'react';
type NavigateOptions={replace?:boolean;reset?:boolean;onCommit?:()=>void};
type Entry={page:string;scroll:number};
const currentPage=()=>location.hash.slice(1)||'home';
const base=(page:string)=>page.split('?')[0];
export function parentPage(page:string){const path=base(page);if(path.startsWith('libraries/')&&path.endsWith('/edit'))return path.slice(0,-5);if(path.startsWith('libraries/'))return 'libraries';if(path.startsWith('projects/'))return 'projects';if(path.startsWith('docs/'))return 'docs';if(path.startsWith('observability/'))return 'observability';if(path.startsWith('task/'))return 'workspace';return 'home';}
export function usePageNavigation(scope:string|null){
 const [page,setPage]=useState(currentPage),[depth,setDepth]=useState(0);
 const project=useRef(scope),trail=useRef<Entry[]>([{page:currentPage(),scroll:0}]),index=useRef(0),active=useRef(currentPage()),owner=useRef(crypto.randomUUID()),restoring=useRef(false),allowedPop=useRef<string|null>(null);
 const state=(position:number)=>({...history.state,lauraNavigation:{owner:owner.current,scope:project.current,depth:position}});
 function guard(next:string,proceed:()=>void){const event=new CustomEvent('laura:before-navigate',{cancelable:true,detail:{next,proceed}});if(window.dispatchEvent(event))proceed();}
 function show(next:string,position:number,scroll=0){active.current=next;index.current=position;setPage(next);setDepth(position);if(!new URLSearchParams(next.split('?')[1]).has('heading'))requestAnimationFrame(()=>window.scrollTo({top:scroll,behavior:'instant'}));}
 useEffect(()=>{
  history.scrollRestoration='manual';history.replaceState(state(0),'',location.href);
  const sync=()=>{if(restoring.current){if(currentPage()===active.current)restoring.current=false;return;}const next=currentPage();if(next===active.current)return;const saved=history.state?.lauraNavigation;const target=saved?.owner===owner.current?saved.depth:0;
   const previous=active.current,previousIndex=index.current;trail.current[previousIndex]={page:previous,scroll:window.scrollY};
   const apply=()=>{let safe=next;if(saved?.scope!==project.current&&project.current!==null&&base(next).startsWith('task/'))safe='workspace';if(safe!==next)history.replaceState(state(target),'','#'+safe);show(safe,target,trail.current[target]?.page===safe?trail.current[target].scroll:0);};
   if(allowedPop.current===next){allowedPop.current=null;apply();return;}
   const delta=previousIndex-target;
   const event=new CustomEvent('laura:before-navigate',{cancelable:true,detail:{next,proceed:()=>{
    if(saved?.owner===owner.current&&delta!==0){allowedPop.current=next;history.go(-delta);}else{history.replaceState(state(target),'','#'+next);apply();}
   }}});
   if(window.dispatchEvent(event))apply();else if(saved?.owner===owner.current&&delta!==0){restoring.current=true;history.go(delta);}else history.replaceState(state(previousIndex),'','#'+previous);
  };
  window.addEventListener('popstate',sync);window.addEventListener('hashchange',sync);return()=>{window.removeEventListener('popstate',sync);window.removeEventListener('hashchange',sync);};
 },[]);
 useEffect(()=>{if(project.current===scope)return;project.current=scope;trail.current=[{page:active.current,scroll:window.scrollY}];index.current=0;setDepth(0);history.replaceState(state(0),'',location.href);},[scope]);
 function navigate(next:string,{replace=false,reset=false,onCommit}:NavigateOptions={}){if(next===active.current&&!reset)return;guard(next,()=>{
  onCommit?.();trail.current[index.current]={page:active.current,scroll:window.scrollY};
  // Document headings and query filters are state within the same page.
  const samePage=base(next)===base(active.current);const overwrite=replace||reset||samePage;
  const position=reset?0:overwrite?index.current:index.current+1;if(reset)trail.current=[];else trail.current=trail.current.slice(0,position+1);trail.current[position]={page:next,scroll:samePage?window.scrollY:0};
  history[overwrite?'replaceState':'pushState'](state(position),'','#'+next);show(next,position,trail.current[position].scroll);
 });}
 function back(){const fallback=parentPage(active.current);if(index.current>0){const previous=trail.current[index.current-1]?.page;if(previous){history.back();return;}}navigate(fallback,{replace:true});}
 return {page,navigate,back,canGoBack:!['home','workspace'].includes(base(page))&&(depth>0||parentPage(page)!==page)};
}
