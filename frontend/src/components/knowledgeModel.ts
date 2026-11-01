import {Data} from '../api/client';
export const sourceTypeOptions=[{value:'product_description',label:'产品说明'},{value:'feature_list',label:'功能清单'},{value:'standard_list',label:'标准清单'},{value:'roadmap',label:'产品规划 / 路线图'},{value:'other',label:'其他'}];
export const sourceTypeLabel=(value:string)=>sourceTypeOptions.find(item=>item.value===value)?.label??'产品说明';
export const buildTitle=(b:Data)=>b.created_at&&Number.isFinite(new Date(b.created_at).getTime())?`构建于 ${new Date(b.created_at).toLocaleString('zh-CN',{hour12:false})}`:'构建时间未记录';
export const buildStageIndex=(b:Data)=>Math.max(0,['queued','preparing','embedding','indexing','completed'].indexOf(b.stage??(['ready','completed','succeeded'].includes(b.status)?'completed':'queued')));
export const buildTerminalMessage=(status:string)=>({ready:'构建完成，等待发布',completed:'构建完成，等待发布',succeeded:'构建完成，等待发布',failed:'构建失败，请查看错误后重试',interrupted:'构建已中断，请查看后台任务状态',cancelled:'构建已取消'} as Record<string,string>)[status]??'';
export const ruleSignature=(rule:Data)=>JSON.stringify(Object.keys(rule).sort().map(k=>[k,rule[k]]));
export function validRule(rule:Data,doc?:Data){if(rule.strategy==='whole_document')return doc&&!/\.(docx|pdf)$/i.test(doc.filename??doc.name??'')?'保留原始文档仅适用于 DOCX 和文字 PDF；Excel 请使用按记录策略':'';if(['excel','excel_records'].includes(rule.strategy)){const s=rule.selection;return !s?.sheet||!s.content_columns?.length?'请选择工作表和内容列':s.row_start>s.row_end?'开始行不能大于结束行':'';}if(rule.strategy==='leaf_sections')return '';if(!Number.isInteger(rule.max_chars)||rule.max_chars<100||rule.max_chars>12000)return '切片长度应为 100～12,000 字符';if(!Number.isInteger(rule.overlap??0)||rule.overlap<0||rule.overlap>=rule.max_chars)return '重叠必须小于切片长度';if(rule.strategy==='delimiter'&&!rule.delimiter?.trim())return '请输入字面分隔符';return '';}
export const productLabel=(p:Data)=>p.family?`${p.family} - ${p.name||p.id}`:p.name||p.id;

export function mergeProductCatalog(current:Data[],incoming:Data[]){const merged=new Map(current.map(p=>[p.id,p]));for(const p of incoming){const prior=merged.get(p.id);if(!prior||(p.version??0)>=(prior.version??0))merged.set(p.id,p);}return [...merged.values()];}
