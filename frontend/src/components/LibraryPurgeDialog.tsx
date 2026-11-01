import {useRef,useState} from 'react';
import {Button,Input,Modal} from 'antd';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';

export default function LibraryPurgeDialog({library,onCancel,onDeleted}:{library:Data;onCancel:()=>void;onDeleted:()=>Promise<void>}){
 const [name,setName]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const running=useRef(false);
 async function remove(){if(running.current||name!==library.name)return;running.current=true;setBusy(true);setError(null);try{await api(`/kb/libraries/${encodeURIComponent(library.id)}`,'DELETE',{expected_version:library.version,confirmation_name:name});await onDeleted();}catch(error){setError(error);}finally{running.current=false;setBusy(false);}}
 return <Modal open title="永久删除知识库" onCancel={onCancel} closable={!busy} keyboard={!busy} mask={{closable:!busy}} footer={<><Button aria-label="取消" disabled={busy} onClick={onCancel}>取消</Button><Button danger type="primary" loading={busy} disabled={busy||name!==library.name} onClick={()=>void remove()}>确认永久删除</Button></>}>
  {library.purge_cleanup_pending?<p>「{library.name}」的记录已移除，文件清理未完成。重试将完成先前已确认的永久删除，无法恢复；此条目只能重试文件清理。</p>:<p>将永久删除「{library.name}」的原件、切片和检索版本，无法恢复。存在历史任务或引用时，服务端会阻止删除并说明原因。</p>}
  <p>请输入完整知识库名称：<strong>{library.name}</strong></p><label>输入知识库名称确认删除<Input aria-label="输入知识库名称确认删除" autoComplete="off" value={name} disabled={busy} onChange={event=>setName(event.target.value)}/></label><ErrorNotice error={error}/>
 </Modal>;
}
