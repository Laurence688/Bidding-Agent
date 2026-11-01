import {Data} from '../api/client';
export const sourceStatus:Record<string,string>={verified_support:'核验支持（子项）',verified_gap:'核验不满足（子项）',candidate:'候选 / 未核验支持'};
export default function ResultSources({sources,onOpen}:{sources:Data[];onOpen:()=>void}){
 if(!sources.length)return <span className="muted">暂无来源</span>;
 return <div className="result-sources">{sources.slice(0,3).map((s,i)=><button className="source-link" key={`${s.chunk_id}:${i}`} onClick={onOpen}>
  <span>{s.source_name}</span><small>{s.location} · {s.version_label}</small><small>{sourceStatus[s.status]??'候选 / 未核验支持'}</small>
 </button>)}{sources.length>3&&<button className="source-link" onClick={onOpen}>查看全部 {sources.length} 个来源</button>}</div>;
}
