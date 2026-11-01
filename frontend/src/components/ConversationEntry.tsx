import {useState} from 'react';
import {Button,Dropdown} from 'antd';
import type {MenuProps} from 'antd';
import {BranchesOutlined,CheckOutlined,DeleteOutlined,EditOutlined,FolderOutlined,MoreOutlined,PushpinFilled,PushpinOutlined,RightOutlined,MessageOutlined} from '@ant-design/icons';
import {Data,displaySessionName} from '../api/client';
import './conversation-sidebar.css';

type Props={session:Data;projects:Data[];selected:boolean;busy:boolean;onSelect:()=>void;onRename:()=>void;onDelete:()=>void;onPin:()=>void;onCopy:(projectId:string)=>void};
export default function ConversationEntry({session,projects,selected,busy,onSelect,onRename,onDelete,onPin,onCopy}:Props){
 const [contextOpen,setContextOpen]=useState(false),[buttonOpen,setButtonOpen]=useState(false);
 const close=()=>{setContextOpen(false);setButtonOpen(false);};
 const items:MenuProps['items']=[
  {key:'rename',icon:<EditOutlined aria-hidden/>,label:'重命名'},
  {key:'pin',icon:session.pinned?<PushpinFilled aria-hidden/>:<PushpinOutlined aria-hidden/>,label:session.pinned?'取消置顶':'置顶'},
  {key:'delete',icon:<DeleteOutlined aria-hidden/>,label:'删除对话',danger:true},
  {type:'divider'},
  {key:'project',popupClassName:'conversation-submenu-popup',icon:<FolderOutlined aria-hidden/>,label:'项目',children:[{type:'group',label:'在目标项目创建历史副本',children:projects.map(p=>({key:'project:'+p.id,icon:p.id===session.project_id?<CheckOutlined aria-hidden/>:<FolderOutlined aria-hidden/>,label:p.name,disabled:p.id===session.project_id}))}]},
  {type:'divider'},
  {key:'branch',popupClassName:'conversation-submenu-popup',icon:<BranchesOutlined aria-hidden/>,label:'分支',children:[{key:'fork',icon:<BranchesOutlined aria-hidden/>,label:'在当前项目创建分支'}]},
 ];
 const container=(trigger:HTMLElement)=>trigger.closest('dialog')??document.body;
 const menu:MenuProps={items,getPopupContainer:container,expandIcon:<RightOutlined aria-hidden style={{fontSize:10}}/>,onClick:({key})=>{close();if(busy)return;if(key==='rename')onRename();else if(key==='pin')onPin();else if(key==='delete')onDelete();else if(key==='fork')onCopy(session.project_id);else if(key.startsWith('project:'))onCopy(key.slice(8));}};
 // 一级菜单和子菜单都挂入手机 dialog 顶层，避免被抽屉遮住。
 const name=displaySessionName(session);
 return <Dropdown menu={menu} trigger={['contextMenu']} disabled={busy} open={contextOpen} onOpenChange={open=>{setContextOpen(open);if(open)setButtonOpen(false);}} getPopupContainer={container} classNames={{root:'conversation-popup'}}>
  <div className={`conversation-entry${selected?' selected':''}`}>
   <button className="conversation-select" aria-current={selected?'true':undefined} title={name} onClick={()=>{close();onSelect();}}><MessageOutlined aria-hidden/><span>{name}</span>{session.pinned&&<PushpinFilled className="conversation-pin" aria-hidden title="已置顶"/>}</button>
   <Dropdown menu={menu} trigger={['click']} open={buttonOpen} disabled={busy} onOpenChange={open=>{setButtonOpen(open);if(open)setContextOpen(false);}} getPopupContainer={container} classNames={{root:'conversation-popup'}}>
    <Button className="conversation-more" type="text" size="small" icon={<MoreOutlined rotate={90}/>} aria-label={`${name} 更多操作`} aria-haspopup="menu" aria-expanded={buttonOpen} loading={busy} disabled={busy}/>
   </Dropdown>
  </div>
 </Dropdown>;
}
