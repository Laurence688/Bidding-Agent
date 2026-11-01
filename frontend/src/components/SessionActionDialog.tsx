import {useRef,useState} from 'react';
import {Alert,Input,Modal} from 'antd';
import type {InputRef} from 'antd';
import {DeleteOutlined,EditOutlined} from '@ant-design/icons';
import {Data,displaySessionName} from '../api/client';
import './session-action-dialog.css';

type Props={session:Data;remove:boolean;onClose:()=>void;onSubmit:(name?:string)=>Promise<void>};
export default function SessionActionDialog({session,remove,onClose,onSubmit}:Props){
 const [name,setName]=useState(()=>displaySessionName(session)),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const input=useRef<InputRef>(null),pending=useRef(false);
 async function submit(){
  if(pending.current||(!remove&&!name.trim()))return;
  pending.current=true;setBusy(true);setError('');
  try{await onSubmit(remove?undefined:name.trim());onClose();}
  catch(e){setError(e instanceof Error?e.message:'操作未完成，请重试');}
  finally{pending.current=false;setBusy(false);}
 }
 return <Modal open centered width={440} className="session-action-dialog" title={<span className="session-action-title"><span aria-hidden="true" className={remove?'session-action-icon danger':'session-action-icon'}>{remove?<DeleteOutlined/>:<EditOutlined/>}</span>{remove?'删除对话':'重命名对话'}</span>}
  onCancel={()=>{if(!pending.current)onClose();}} onOk={()=>void submit()} okText={remove?'确认删除':'保存名称'} cancelText="取消" confirmLoading={busy}
  okButtonProps={{danger:remove,disabled:!remove&&!name.trim()}} cancelButtonProps={{disabled:busy,'aria-label':'取消'}} closable={!busy} keyboard={!busy} mask={{closable:false}}
  afterOpenChange={open=>{if(open&&!remove)input.current?.focus({cursor:'all'});}}>
  {remove?<><p className="session-action-description">删除后，此对话中的未完成任务会取消。</p><div className="session-action-name">{displaySessionName(session)}</div><p className="session-action-note">项目审核成果会保留。</p></>:<><p className="session-action-description">用一个清晰的名称，方便下次找到这段对话。</p><label htmlFor="session-name-input" className="session-action-label">对话名称</label><Input ref={input} id="session-name-input" value={name} maxLength={100} showCount disabled={busy} placeholder="输入对话名称" onChange={e=>setName(e.target.value)} onPressEnter={e=>{if(!e.nativeEvent.isComposing){e.preventDefault();void submit();}}}/></>}
  {error&&<Alert className="session-action-error" type="error" showIcon title={error}/>}
 </Modal>;
}
