import {useState} from 'react';
import {Dropdown,Input} from 'antd';
import {Data} from '../api/client';
import {Icon} from './Icon';
export type ProjectSelectorProps={projects:Data[];projectId?:string;onChooseProject:(id:string)=>void;onCreateProject:()=>void;onManageProjects:()=>void};
export default function ProjectSelector({projects,projectId,onChooseProject,onCreateProject,onManageProjects}:ProjectSelectorProps){
 const [open,setOpen]=useState(false),[query,setQuery]=useState('');const current=projects.find(p=>p.id===projectId);
 const choose=(action:()=>void)=>{setOpen(false);setQuery('');action();};
 return <Dropdown trigger={['click']} open={open} onOpenChange={setOpen} popupRender={()=><div className="project-selector-panel" role="dialog" aria-label="选择项目" onKeyDown={e=>{if(e.key==='Escape')setOpen(false);}}><Input autoFocus prefix={<Icon name="search" size={14}/>} aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={e=>setQuery(e.target.value)}/><div role="listbox" aria-label="项目列表">{projects.filter(p=>p.name.toLowerCase().includes(query.toLowerCase())).map(p=><button type="button" role="option" aria-selected={p.id===projectId} key={p.id} title={p.name} onClick={()=>choose(()=>onChooseProject(p.id))}><span>{p.name}</span>{p.id===projectId&&<Icon name="check" size={14}/>}</button>)}{!projects.some(p=>p.name.toLowerCase().includes(query.toLowerCase()))&&<p>未找到项目</p>}</div><footer><button type="button" onClick={()=>choose(onCreateProject)}><Icon name="plus" size={14}/>新建项目</button><button type="button" onClick={()=>choose(onManageProjects)}><Icon name="settings" size={14}/>管理项目</button></footer></div>}><button type="button" className="project-selector" aria-label="当前项目" aria-haspopup="dialog" aria-expanded={open} title={current?.name??'选择项目'}><Icon name="folder" size={15}/><span>{current?.name??'选择项目'}</span><Icon name="chevron-down" size={12}/></button></Dropdown>;
}
