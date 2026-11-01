import {useEffect,useState} from 'react';
import {Select,Alert,Button} from 'antd';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';
export default function ProjectBindings({libraryId,productId,disabled=false,onSaved}:{libraryId:string;productId?:string;disabled?:boolean;onSaved?:()=>void}){
 const [value,setValue]=useState<Data|null>(null),[ids,setIds]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[saved,setSaved]=useState(false);
 useEffect(()=>{let active=true;setValue(null);api(`/kb/libraries/${libraryId}/projects`).then(v=>{if(active){setValue(v);setIds(v.project_ids);}}).catch(e=>active&&setError(e));return()=>{active=false;};},[libraryId]);
 return <section className="project-bindings"><h2>项目绑定</h2><p>一次保存本库的项目关联；各项目的其他知识库和历史任务不受影响。</p><ErrorNotice error={error}/><Select mode="multiple" showSearch optionFilterProp="label" aria-label="绑定项目" style={{width:'100%'}} value={ids} disabled={disabled||busy||!value} placeholder="搜索并选择项目" options={(value?.projects??[]).map((p:Data)=>({value:p.id,label:p.name??p.id}))} onChange={v=>{setIds(v);setSaved(false);}}/>{value?.projects?.some((p:Data)=>ids.includes(p.id)&&p.default_product_id!==productId)&&<Alert type="info" title="项目默认型号不同仍可绑定；任务只使用所选产品型号的资料。"/>}<Button disabled={disabled||busy||!value} loading={busy} onClick={async()=>{setBusy(true);setError(null);try{const v=await api(`/kb/libraries/${libraryId}/projects`,'PATCH',{expected_version:value?.expected_version,project_ids:ids});setValue(v);setIds(v.project_ids);setSaved(true);onSaved?.();}catch(e){setError(e);}finally{setBusy(false);}}}>保存项目绑定</Button>{saved&&<p role="status">项目绑定已保存</p>}</section>;
}
