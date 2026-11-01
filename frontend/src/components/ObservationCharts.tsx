import {Data,label} from '../api/client';

export const number=(n:number|null|undefined)=>n==null?'未知':n.toLocaleString('zh-CN');
export const duration=(n:number|null|undefined)=>n==null?'未知':n<60?`${n.toFixed(1)} 秒`:n<3600?`${(n/60).toFixed(1)} 分钟`:`${(n/3600).toFixed(2)} 小时`;
export const timestamp=(value:string|null)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未知';
export const tokens=(usage:Data)=>!usage.attempt_count?'0（无调用）':usage.total_tokens!=null?number(usage.total_tokens):usage.known_total_tokens!=null?`${number(usage.known_total_tokens)} + 未知`:'未知';
export const metricText=(metric:Data)=>`${metric.value==null?'—':`${(metric.value*100).toFixed(1)}%`}（${metric.numerator} / ${metric.denominator}）`;
export const modeName=(mode:string)=>({PRODUCT_QA:'产品问答',SOC_SINGLE:'单条 SOC',SOC_BATCH:'Excel 批量',TRANSLATION:'标书翻译',TENDER_ANALYSIS:'标书拆解'}[mode]??mode);
export const stateOrder=['running','queued','completed','waiting_review','failed','interrupted','cancelled','unknown'];
export const stateColors:Record<string,string>={running:'#155eef',queued:'#8aa3c6',completed:'#167954',waiting_review:'#a76a13',failed:'#b54440',interrupted:'#79629d',cancelled:'#8a94a3',unknown:'#b6c1cf'};

export function QualityCards({quality}:{quality:Data}){
 const definitions=[['adoption','采纳率','已批准 / 已生成 SOC 条目'],['accuracy','准确率（人工标注）','标注正确 / 明确标注正确或错误'],['intervention','人工介入率','额外处理条目 / 已生成 SOC 条目']];
 return <div className="ob-quality-grid">{definitions.map(([key,title,description])=>{const metric=quality[key],value=metric.value;return <section className={`ob-quality ob-quality-${key}`} key={key} aria-label={title}>
  <div><h2>{title}</h2><strong>{value==null?'暂无数据':`${(value*100).toFixed(1)}%`}</strong><p>{metric.numerator} / {metric.denominator} 条 · {description}</p></div>
  <svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="25" fill="none" stroke="#e9eef6" strokeWidth="5"/><circle cx="32" cy="32" r="25" fill="none" stroke="currentColor" strokeWidth="5" strokeDasharray={`${(value??0)*157.08} 157.08`} transform="rotate(-90 32 32)"/><text x="32" y="36" textAnchor="middle">{value==null?'—':`${Math.round(value*100)}%`}</text></svg>
 </section>;})}</div>;
}

export function UsageTrend({days,view}:{days:Data[];view:'tokens'|'time'}){
 const values=days.map(day=>view==='tokens'?day.usage.known_total_tokens:day.usage.known_model_latency_seconds);
 const max=Math.max(1,...values.map(n=>n??0));
 const chartTitle=view==='tokens'?'近 14 天已知 Token 用量':'近 14 天已知模型耗时';
 return <><svg className="ob-trend" viewBox="0 0 620 208" role="img" aria-label={chartTitle}>
  <title>{chartTitle}，按北京时间的调用开始日期统计；缺失值不补零。</title>
  {[0,.5,1].map(p=><g key={p}><line x1="48" x2="610" y1={160-p*130} y2={160-p*130} stroke="#e2e9f3" strokeDasharray={p?'3 4':undefined}/><text x="42" y={164-p*130} textAnchor="end" className="ob-axis">{view==='tokens'?number(Math.round(max*p)):`${Math.round(max*p)}s`}</text></g>)}
  {days.map((day,i)=>{const x=56+i*39.3,u=day.usage,value=values[i],height=(value??0)/max*130,missing=u.missing_attempt_count>0;return <g key={day.date}>
   <title>{day.date}：{view==='tokens'?tokens(u):duration(value)}；调用 {u.attempt_count} 次；{missing?`${u.missing_attempt_count} 次用量缺失`:'用量无缺失'}</title>
   <rect x={x} y={160-height} width="22" height={height} rx="3" fill={view==='tokens'?'#155eef':'#167954'}/>
   {(missing||(u.attempt_count>0&&value==null))&&<circle cx={x+11} cy={155-height} r="3" fill="#a76a13"/>}
   {i%2===0&&<text x={x+11} y="185" textAnchor="middle" className="ob-axis">{day.date.slice(5)}</text>}
  </g>;})}
 </svg><div className="ob-chart-caption"><span><i className={`ob-key ${view}`}/> {view==='tokens'?'供应商已返回的 Token':'已记录的模型请求耗时'}</span><span><i className="ob-key missing"/> 有缺失记录</span></div>
 <details className="ob-data-details"><summary>查看每日数值</summary><div className="table-scroll"><table><thead><tr><th>日期（北京时间）</th><th>调用次数</th><th>Token</th><th>模型耗时（已知）</th></tr></thead><tbody>{days.map(d=><tr key={d.date}><td>{d.date}</td><td>{d.usage.attempt_count}</td><td>{tokens(d.usage)}</td><td>{d.usage.attempt_count?duration(d.usage.known_model_latency_seconds):'0 秒'}</td></tr>)}</tbody></table></div></details></>;
}

export function NodeChart({nodes}:{nodes:Data[]}){
 const max=Math.max(1,...nodes.map(n=>n.usage.known_model_latency_seconds??0));
 return <div className="ob-node-chart" role="img" aria-label="模型节点耗时分布">{!nodes.length?<p className="muted">有模型调用后显示各节点耗时。</p>:nodes.map(n=><div className="ob-node" key={n.node}><div><span>{label(n.node)}</span><strong>{duration(n.usage.known_model_latency_seconds)}</strong></div><div className="ob-node-track"><span style={{width:`${(n.usage.known_model_latency_seconds??0)/max*100}%`}}/></div><small>{n.usage.attempt_count} 次调用 · {n.failed} 次失败 · {tokens(n.usage)} Token</small></div>)}</div>;
}
