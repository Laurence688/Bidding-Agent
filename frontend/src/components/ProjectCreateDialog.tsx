import {useEffect,useRef,useState} from 'react';
import {Data} from '../api/client';
import {productLabel} from './knowledgeModel';
import {ErrorNotice} from './Status';

type Props={products:Data[];name:string;product:string;onName:(v:string)=>void;onProduct:(v:string)=>void;onClose:()=>void;onRegister:()=>void;onSubmit:()=>Promise<void>};
export default function ProjectCreateDialog({products,name,product,onName,onProduct,onClose,onRegister,onSubmit}:Props){
 const dialog=useRef<HTMLDialogElement>(null),pending=useRef(false);
 const [error,setError]=useState<unknown>(null),[busy,setBusy]=useState(false);
 useEffect(()=>{const element=dialog.current;if(element){if(element.showModal)element.showModal();else element.setAttribute('open','');element.querySelector('input')?.focus();}},[]);
 return <dialog ref={dialog} onCancel={e=>{if(busy)e.preventDefault();else onClose();}} className="project-create-dialog"><form onSubmit={async e=>{e.preventDefault();if(pending.current||!products.length||!name.trim())return;pending.current=true;setBusy(true);setError(null);try{await onSubmit();}catch(e){setError(e);}finally{pending.current=false;setBusy(false);}}}><h2>新建项目</h2><ErrorNotice error={error} onDismiss={()=>setError(null)}/><label>项目名称<input required maxLength={100} disabled={busy} value={name} onChange={e=>{setError(null);onName(e.target.value);}}/></label>{products.length?<label>默认产品型号<select disabled={busy} value={product||products[0]?.id||''} onChange={e=>{setError(null);onProduct(e.target.value);}}>{products.map(p=><option key={p.id} value={p.id}>{productLabel(p)}</option>)}</select></label>:<p role="status">请先登记产品型号，再创建项目。</p>}<button type="button" disabled={busy} onClick={onRegister}>去登记产品</button><div className="actions"><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy||!products.length||!name.trim()}>{busy?'正在创建…':'创建项目'}</button></div></form></dialog>;
}
