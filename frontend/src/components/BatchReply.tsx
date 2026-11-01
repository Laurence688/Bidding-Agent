import {useEffect,useState} from 'react';
import {Collapse,Pagination,Tag,Button} from 'antd';
import {api,Data} from '../api/client';
import ResponseBlock from './ResponseBlock';
export default function BatchReply({runId,job,timestamp,onReference,onReview}:{onReview?:(clauseId:string)=>void;onReference?:(clauseId:string)=>void;runId:string;job?:Data;timestamp?:string}){
 const [open,setOpen]=useState(true),[rows,setRows]=useState<Data[]>([]),[page,setPage]=useState(1),[total,setTotal]=useState(0),[error,setError]=useState('');
 useEffect(()=>{setOpen(true);setRows([]);setPage(1);},[runId]);
 useEffect(()=>{if(!open)return;let alive=true;const load=async()=>{const run=await api(`/runs/${runId}`);const result=await api(`/projects/${run.project_id}/review-workbench?run_id=${runId}&review_status=all&limit=10&cursor=${(page-1)*10}`);if(alive){setRows(result.items);setTotal(result.total);}};void load().catch(e=>alive&&setError(e.message));return()=>{alive=false;};},[open,page,runId,job?.version]);
 return <div><p>Excel 批次已处理 {job?.processed_items??0} / {job?.total_items??'未知'} 条。审核与正式导出请进入“效果评估 → 应答审核”。</p><Collapse activeKey={open?['rows']:[]} onChange={keys=>setOpen(keys.includes('rows'))} items={[{key:'rows',label:'展开逐条应答 · 分页查看',children:<>{error&&<p role="alert">{error}</p>}{rows.map(row=><article className="upgrade-plain-list" key={row.id}><p>{row.raw_text} <Tag>{row.review_status==='approved'?'已审核':'待审核'}</Tag></p>{row.result?<ResponseBlock timestamp={timestamp} result={row.result} revised={row.manually_revised} reviewStatus={row.review_status} onReview={onReview?()=>onReview(row.clause_id):undefined} onReference={onReference&&job?.status==='completed'?()=>onReference(row.clause_id):undefined}/>:<p>尚未完成</p>}</article>)}<Pagination current={page} pageSize={10} total={total} onChange={setPage} showSizeChanger={false}/></>} ]}/></div>;
}
