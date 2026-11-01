import {useEffect,useState} from 'react';
import {api,Data} from '../api/client';
import {ErrorNotice} from '../components/Status';
import './libraries.css';

export default function EmbeddingSettings(){
 const [profiles,setProfiles]=useState<Data[]>([]),[error,setError]=useState<unknown>(null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[creating,setCreating]=useState(false);
 const [name,setName]=useState(''),[model,setModel]=useState(''),[maxChars,setMaxChars]=useState(1000),[maxLength,setMaxLength]=useState(1024),[batchSize,setBatchSize]=useState(4);
 const [checks,setChecks]=useState<Record<string,Data>>({});
 useEffect(()=>{let active=true;api('/kb/embedding-profiles').then(v=>active&&setProfiles(v.items.filter((p:Data)=>p.mode==='local'))).catch(e=>active&&setError(e));return()=>{active=false;};},[]);
 async function check(profile:Data){if(busy)return;setBusy(true);setError(null);try{const result=await api(`/kb/embedding-profiles/${profile.id}/check`,'POST',{allow_cloud:false});setChecks(old=>({...old,[profile.id]:result}));setMessage('连接校验已完成，请查看对应配置的结果。');}catch(e){setError(e);}finally{setBusy(false);}}
 async function save(e:React.FormEvent){e.preventDefault();if(busy)return;setBusy(true);setError(null);setMessage('');try{await api('/kb/embedding-profiles','POST',{name:name.trim(),mode:'local',model:model.trim(),max_chars:maxChars,max_length:maxLength,batch_size:batchSize});setProfiles((await api('/kb/embedding-profiles')).items.filter((p:Data)=>p.mode==='local'));setCreating(false);setMessage('配置已保存。连接仍需单独校验，新配置用于后续新建索引。');}catch(e){setError(e);}finally{setBusy(false);}}
 return <div className="library-page embedding-page"><div className="page-heading"><div><p className="eyebrow">系统设置 / 检索计算</p><h1>向量模型</h1><p>构建和查询使用同一版本配置。更换模型后构建新索引，再发布切换。</p></div><button className="primary" onClick={()=>setCreating(v=>!v)}>新增向量模型</button></div><ErrorNotice error={error}/>{message&&<p className="notice" role="status">{message}</p>}
 <p className="library-scope">当前版本仅使用本机已有向量模型。保存配置后需单独校验；更换模型不会改写历史索引。</p>
 {creating&&<form className="library-form embedding-form" onSubmit={save}><h2>新增配置</h2><label>配置名称<input required value={name} onChange={e=>setName(e.target.value)}/></label><label>本地模型路径<input required value={model} onChange={e=>setModel(e.target.value)}/></label>
 <label>切片字符上限<input type="number" required min={100} max={12000} value={maxChars} onChange={e=>setMaxChars(Number(e.target.value))}/></label><label>模型输入 Token 上限<input type="number" min={128} max={32768} value={maxLength} onChange={e=>setMaxLength(Number(e.target.value))}/></label><label>本地计算批次<input type="number" min={1} max={64} value={batchSize} onChange={e=>setBatchSize(Number(e.target.value))}/></label><p>上限应适合模型输入；无法容纳的片段应重新切分，不静默截断。模型权重需预先准备，本页面不承诺自动下载或离线安装包。</p><div className="actions"><button type="button" disabled={busy} onClick={()=>{setCreating(false);}}>取消</button><button className="primary" disabled={busy}>保存配置</button></div></form>}
 <div className="embedding-profiles">{profiles.map(profile=><article key={profile.id}><div className="section-bar"><div><h2>{profile.name??profile.model}</h2><span className="embedding-mode">本地计算</span></div><button disabled={busy} onClick={()=>check(profile)}>校验连接</button></div><dl><div><dt>模型</dt><dd>{profile.model}</dd></div>{profile.base_url&&<div><dt>服务地址</dt><dd>{profile.base_url}</dd></div>}<div><dt>输入上限</dt><dd>{profile.max_chars??'按运行配置'} 字符</dd></div></dl>
 <p className="embedding-used">使用此配置的库：{profile.used_by_libraries?.length?profile.used_by_libraries.map((l:Data|string)=>typeof l==='string'?l:l.name??l.id).join('、'):'暂无已登记绑定'}</p>
 {checks[profile.id]&&<div className="embedding-check" role="status"><strong>{checks[profile.id].ok===false?'连接未通过':'校验结果'}</strong><p>{checks[profile.id].message??checks[profile.id].status??(checks[profile.id].ok?'连接正常':'请查看返回状态')}</p>{checks[profile.id].dimensions&&<small>向量维度：{checks[profile.id].dimensions}</small>}</div>}
 </article>)}</div>{!profiles.length&&<p className="empty">尚无向量模型，请添加本地模型。</p>}
 </div>;
}
