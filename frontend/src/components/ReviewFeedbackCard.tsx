import {useEffect,useId,useMemo,useState} from 'react';
import {api,Data} from '../api/client';
import {ErrorNotice} from './Status';
import './review-feedback.css';

const pageSize=30;
const statusLabels:Record<string,string>={proposed:'建议，待验证',pending:'待验证',not_verified:'尚未验证',verified:'已验证'};
const verdictLabels:Record<string,string>={correct:'批注表示正确',incorrect:'批注指出问题',uncertain:'待进一步核对',not_requirement:'标为不需应答',needs_review:'待进一步核对'};
const sourceCell=(row:Data)=>{
 const cell=row.source_cell??row.cell;
 if(!cell)return '未记录批注位置';
 return cell.includes('!')||!row.coordinate?.sheet?cell:`${row.coordinate.sheet}!${cell}`;
};
const inputCell=(row:Data)=>row.coordinate?.input_cell?`${row.coordinate.sheet}!${row.coordinate.input_cell}`:'未记录原文位置';

export default function ReviewFeedbackCard({feedback,onTask}:{feedback:Data;onTask?:(id:string)=>void}){
 const headingId=useId(),[query,setQuery]=useState(''),[visible,setVisible]=useState(pageSize);
 const links:Data[]=feedback.annotation_links??[],stats:Data=feedback.import_summary??{};
 const matches=useMemo(()=>{
  const search=query.trim().toLocaleLowerCase();
  return search?links.filter(row=>[row.notes,row.raw_text,sourceCell(row),inputCell(row)].some(value=>String(value??'').toLocaleLowerCase().includes(search))):links;
 },[links,query]);
 useEffect(()=>{setQuery('');setVisible(pageSize);},[feedback.id]);
 const conflicts:Data[]=stats.conflicts??[],blanks:Data[]=Array.isArray(stats.blank_rows)?stats.blank_rows:[];
 const conflictLabel=conflicts.length>0&&conflicts.every(row=>row.machine_decision==='FC')?'现有 FC ':'现有结论';
 return <section className="panel review-feedback" aria-labelledby={headingId}>
  <header className="section-bar"><div><p className="eyebrow">人工复盘记录</p><h2 id={headingId}>{feedback.title??'人工批注复盘'}</h2></div>{onTask&&feedback.task_id&&<button onClick={()=>onTask(feedback.task_id)}>打开对应任务</button>}</header>
  {feedback.summary&&<p>{feedback.summary}</p>}
  {(feedback.implementation_updates??[]).map((update:Data)=><section className="notice" aria-label="改进执行进展" key={update.id}><h3>改进执行进展 · {new Date(update.created_at).toLocaleDateString('zh-CN')}</h3><p>{update.summary}</p><ul>{update.items.map((item:string,i:number)=><li key={i}>{item}</li>)}</ul></section>)}
  <p className="review-feedback-source"><span>来源：<strong>{feedback.source_filename??'未记录文件名'}</strong></span>{feedback.created_at&&<time dateTime={feedback.created_at}>{new Date(feedback.created_at).toLocaleString('zh-CN')}</time>}</p>
  <div className="review-feedback-counts" aria-label="批注导入统计">
   <span><strong>{stats.annotated_rows??links.length}{stats.total_rows!=null?` / ${stats.total_rows}`:''}</strong>已关联批注 {stats.annotated_rows??links.length} 条</span>
   {stats.blank_count!=null&&<span><strong>{stats.blank_count}</strong>批注空白 {stats.blank_count} 条</span>}
   {stats.conflict_count!=null&&<span className={stats.conflict_count?'review-feedback-attention':''}><strong>{stats.conflict_count}</strong>批注与{conflictLabel}不一致 {stats.conflict_count} 处</span>}
   {stats.not_requirement_count!=null&&<span><strong>{stats.not_requirement_count}</strong>标为不需应答 {stats.not_requirement_count} 条</span>}
  </div>
  {stats.classification_note&&<p className="muted">{stats.classification_note}</p>}
  {conflicts.length>0&&<details className="review-feedback-exceptions"><summary>核对批注与现有结论不一致的条目</summary><p className="muted">以下仅记录两者的差异，需结合原文和证据复核。</p>{conflicts.map((row,i)=><article key={i}><p><span className="mono">{sourceCell(row)}</span> · 现有结论：{row.machine_decision??'未记录'}</p><blockquote>{row.notes}</blockquote><p className="review-feedback-original">{row.raw_text}</p></article>)}</details>}
  {blanks.length>0&&<details className="review-feedback-exceptions"><summary>查看尚未填写批注的条目</summary>{blanks.map((row,i)=><article key={i}><p className="mono">{sourceCell(row)}</p><p className="review-feedback-original">{row.raw_text}</p></article>)}</details>}
  {(feedback.source_issues??[]).length>0&&<section className="review-feedback-issues" aria-label="Sheet1 问题原话"><h3>Sheet1 问题原话</h3>{feedback.source_issues.map((issue:Data,i:number)=><article key={`${issue.cell}-${i}`}><span className="mono">{issue.cell}</span><blockquote>{issue.text}</blockquote></article>)}</section>}
  {(feedback.improvements??[]).length>0&&<section className="review-feedback-improvements" aria-label="改进建议与验收"><h3>改进建议与验收</h3><ol>{feedback.improvements.map((item:Data,i:number)=><li key={i}><div className="review-feedback-improvement-heading">{item.priority&&<span className="badge">{item.priority}</span>}<h4>{item.title}</h4><span className="review-feedback-validation">验证状态：{statusLabels[item.evidence_status]??item.evidence_status??'待验证'}</span></div><dl><dt>建议措施</dt><dd>{item.action}</dd><dt>验收标准</dt><dd>{item.acceptance??'待补充'}</dd></dl></li>)}</ol></section>}
  <details className="review-feedback-annotations"><summary>查看人工批注（{links.length} 条）</summary>
   <label>搜索人工批注<input type="search" value={query} onChange={e=>{setQuery(e.target.value);setVisible(pageSize);}} placeholder="搜索批注原话、条款或单元格位置"/></label>
   <p className="muted" role="status">匹配 {matches.length} 条，已显示 {Math.min(visible,matches.length)} 条。分类仅为辅助整理，原话是复核依据。</p>
   {matches.length===0?<p>没有匹配的人工批注。</p>:<ol className="review-feedback-annotation-list">{matches.slice(0,visible).map((row,i)=><li key={row.annotation_id??`${row.clause_id}-${i}`}><div className="review-feedback-location"><span>批注位置：<span className="mono">{sourceCell(row)}</span></span><span>原文位置：<span className="mono">{inputCell(row)}</span></span></div><blockquote>{row.notes}</blockquote><p className="review-feedback-original">{row.raw_text}</p>{row.verdict&&<p className="muted">导入分类（辅助）：{verdictLabels[row.verdict]??row.verdict}{row.expected_decision?` · 标注期望结论：${row.expected_decision}`:''}</p>}</li>)}</ol>}
   {visible<matches.length&&<button onClick={()=>setVisible(count=>count+pageSize)}>加载更多批注</button>}
  </details>
  {(stats.category_counts??[]).length>0&&<details><summary>按批注原话汇总</summary><div className="table-scroll"><table><thead><tr><th>批注原话</th><th>数量</th><th>现有机器结论</th></tr></thead><tbody>{stats.category_counts.map((row:Data,i:number)=><tr key={i}><td>{row.comment}</td><td>{row.count}</td><td>{Object.entries(row.machine_counts??{}).map(([key,count])=>`${key} ${count}`).join(' · ')||'未记录'}</td></tr>)}</tbody></table></div></details>}
  <details className="review-feedback-provenance"><summary>来源与运行标识</summary><p>来源文件：{feedback.source_filename??'未记录'}</p><p className="mono">SHA-256：{feedback.source_sha256??'未记录'}<br/>运行：{feedback.run_id??'未记录'}<br/>反馈：{feedback.id??'未记录'}</p></details>
 </section>;
}

export function RunReviewFeedbacks({runId,revision}:{runId:string;revision:string}){
 const [retry,setRetry]=useState(0),[view,setView]=useState<{runId:string;items:Data[];loading:boolean;error:unknown}>({runId,items:[],loading:true,error:null});
 useEffect(()=>{
  let active=true;
  setView(old=>({runId,items:old.runId===runId?old.items:[],loading:true,error:null}));
  api(`/runs/${runId}/review-feedbacks`).then(value=>{
   if(active)setView({runId,items:value.items,loading:false,error:null});
  }).catch(error=>{if(active)setView(old=>({...old,loading:false,error}));});
  return()=>{active=false;};
 },[runId,revision,retry]);
 if(view.runId!==runId)return null;
 return <><ErrorNotice error={view.error} onRefresh={()=>setRetry(value=>value+1)}/>{view.loading&&view.items.length===0&&<p className="muted" role="status">正在读取复盘反馈…</p>}{view.items.map(feedback=><ReviewFeedbackCard key={feedback.id} feedback={feedback}/>)}</>;
}
