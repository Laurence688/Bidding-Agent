export type Data = Record<string, any>;
export const displaySessionName=(session:Data)=>session.name&&session.name!=='新会话'?session.name:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(session.created_at));
let token = '';
export class ApiError extends Error {
  constructor(public status:number,public code:string,message:string,public details:Data){super(message);}
}
export async function bootstrap(){const value=await api('/bootstrap');token=value.token;return value;}
export async function api(path:string,method='GET',body?:unknown,key?:string,options?:{signal?:AbortSignal}):Promise<any>{
  const form=body instanceof FormData;
  const headers:Record<string,string>={'X-Local-Token':token};
  if(body&&!form) headers['Content-Type']='application/json';
  if(method!=='GET') headers['Idempotency-Key']=key??crypto.randomUUID();
  const response=await fetch('/api/v1'+path,{method,headers,signal:options?.signal,body:body===undefined?undefined:form?body:JSON.stringify(body)});
  if(!response.ok){let error:Data={};try{error=(await response.json()).error??{};}catch{ /* 网关没有返回 JSON。 */ }
    throw new ApiError(response.status,error.code??'REQUEST_FAILED',error.message??'请求未完成，请稍后重试',error.details??{});}
  return response.json();
}
export async function downloadExport(id:string){
  const response=await fetch(`/api/v1/exports/${id}/download`,{headers:{'X-Local-Token':token}});
  if(!response.ok){const data=await response.json();throw new Error(data.error.message);}
  const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`SOC-${id.slice(0,8)}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function downloadRunReport(id:string){
 const response=await fetch(`/api/v1/runs/${id}/report/download`,{headers:{'X-Local-Token':token}});
 if(!response.ok)throw new Error('运行日志下载失败，请刷新后重试');
 const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`SOC-run-${id.slice(0,8)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function downloadReviewWorkbook(id:string){
 const response=await fetch(`/api/v1/runs/${id}/report/workbook`,{headers:{'X-Local-Token':token}});
 if(!response.ok)throw new Error('审阅 Excel 下载失败，请刷新后重试');
 const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`SOC-review-${id.slice(0,8)}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const label=(code:string)=>({succeeded:'调用成功',in_flight:'请求中',unknown:'未记录',product_qa:'组织应答',document_translate:'翻译文本',tender_extract:'解析标书',qa_answer:'产品问答',validate_input:'核对输入',translate:'辅助翻译',split_claims:'拆分要求',retrieve:'检索资料',admit_evidence:'筛选适用证据',judge_claim:'逐项判断',verify_citations:'核验引用',verify_semantic_support:'核验语义支持',supplement_retrieve:'补充检索',aggregate:'汇总判断',render_response:'起草英文口径',validate_output:'核验最终口径',commit_result:'保存结果',UNKNOWN:'待补证 / 未决',NOT_REVIEWED:'待人工审核',OUTPUT_INVALID:'口径校验未通过',CITATION_INVALID:'引用校验未通过',SOURCE_INVALID:'来源已失效',PRODUCT_CONFLICT:'型号冲突',RESULT_MISSING:'尚未生成结果',NO_EXCEL_MAPPING:'单条结果无需 Excel 导出',RUN_SUPERSEDED:'历史运行已只读',REVIEW_INVALIDATED:'审核已失效',MODEL_CONFIG_MISSING:'尚未配置模型密钥',EVIDENCE_MISSING:'缺少适用证据',CLAIM_INCOMPLETE:'要求拆分不完整',MODEL_UNAVAILABLE:'模型调用未完成',ROADMAP_ONLY:'仅有规划资料',PROJECT_FACT_MISSING:'缺少项目事实证据',INPUT_CHANGED:'输入文件已变化',queued:'排队中',running:'处理中',completed:'处理完成',interrupted:'已中断',failed:'执行失败',partial:'部分完成',TRANSLATION:'翻译',TENDER_ANALYSIS:'标书拆解',cancelled:'已取消',waiting_review:'待处理型号冲突',pending:'待审核',approved:'已审核',revoked:'已撤销'} as Record<string,string>)[code]??code;

export async function downloadDocumentArtifact(id:string,name='Laura-result'){
 const response=await fetch(`/api/v1/document-tasks/artifacts/${encodeURIComponent(id)}/download`,{headers:{'X-Local-Token':token}});
 if(!response.ok)throw new Error('产物下载未完成，请刷新任务后重试');
 const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export async function streamJob(jobId:string,cursor:number,signal:AbortSignal,onEvent:(id:number)=>void){
 const response=await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/events`,{headers:{'X-Local-Token':token,'Last-Event-ID':String(cursor)},signal});
 if(!response.ok)throw new ApiError(response.status,response.status===409?'EVENT_RESYNC_REQUIRED':'STREAM_FAILED','连接中断',{});
 if(!response.body)throw new Error('流不可用');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',last=cursor;
 try{while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');let end;
  while((end=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);const match=/^id:\s*(\d+)/m.exec(frame);if(match&&Number(match[1])>last){last=Number(match[1]);onEvent(last);}}
 }}finally{reader.releaseLock();}
}
/** Authenticated documentation images never put the token in URLs. Caller owns revocation. */
export async function documentAsset(id:string,signal:AbortSignal):Promise<string>{
 const response=await fetch('/api/v1/docs/assets/'+encodeURIComponent(id),{headers:{'X-Local-Token':token},signal});
 if(!response.ok)throw new Error('截图加载失败');
 const blob=await response.blob();if(signal.aborted)throw new DOMException('Aborted','AbortError');
 return URL.createObjectURL(blob);
}
