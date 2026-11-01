import {useEffect,useRef,useState} from 'react';
import {Button} from 'antd';
import HomeContact from '../components/HomeContact';
import {ArrowRightOutlined,DatabaseOutlined,FileDoneOutlined,ExperimentOutlined,TranslationOutlined,FileSearchOutlined} from '@ant-design/icons';

import './home-upgrade.css';

const phases=['原始条目','要求识别','混合检索','证据核验','四态判定','英文口径','待人工审核'];

export default function Home({onNavigate}:{onNavigate:(page:string)=>void}){
 const host=useRef<HTMLElement>(null);
 const [visible,setVisible]=useState(true),[tabVisible,setTabVisible]=useState(!document.hidden),[reduced,setReduced]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches);

 useEffect(()=>{
  const media=window.matchMedia('(prefers-reduced-motion: reduce)'),change=()=>setReduced(media.matches);
  media.addEventListener('change',change);
  const visibility=()=>setTabVisible(!document.hidden);
  document.addEventListener('visibilitychange',visibility);
  const observer=new IntersectionObserver(entries=>setVisible(entries[0]?.isIntersecting??false),{threshold:.15});
  if(host.current)observer.observe(host.current);
  return()=>{media.removeEventListener('change',change);document.removeEventListener('visibilitychange',visibility);observer.disconnect();};
 },[]);

 return <div className="laura-home">
  <section className="laura-hero">
   <header className="hero-head">
    <h1 aria-label="Laura 你的投标助手"><span className="hero-line hero-line-brand">Laura</span><span className="hero-line hero-line-role">你的投标助手</span></h1>
    <p className="hero-line-sub">从读懂标书，到生成有据可查的应答。</p>
    <div className="hero-actions">
     <Button type="primary" size="large" icon={<ArrowRightOutlined aria-hidden="true"/>} iconPlacement="end" onClick={()=>onNavigate('workspace')}>进入工作中心</Button>
     <Button size="large" onClick={()=>onNavigate('docs')}>使用指南</Button>
    </div>
   </header>
   <section className={`laura-preview${reduced?' static-demo':''}${visible&&tabVisible?'':' is-frozen'}`} ref={host} aria-label="合成条款处理流程示例">
    <div className="preview-rail" aria-hidden="true">
     <span className="preview-rail-line"><i className="preview-rail-glow"/><i className="preview-rail-dot"/></span>
    </div>
    <ol className="preview-steps">
     {phases.map((name,index)=>(
      <li key={name}><i aria-hidden="true"/><span>{name}</span></li>
     ))}
    </ol>
    <div className="preview-flow">
     <div className="preview-node preview-node-in">
      <span className="preview-node-cap">原始条目</span>
      <span className="preview-doc"><i/>合成标书要求</span>
      <span className="preview-hint">4 条要求并行处理</span>
     </div>
     <div className="preview-link" aria-hidden="true"><i/><i/><i/></div>
     <div className="preview-node preview-node-claim">
      <span className="preview-node-cap">要求识别</span>
      <span className="preview-claim"><i/>交换容量 ≥ 5120 Gbps</span>
      <span className="preview-hint">数值与条件保留</span>
     </div>
     <div className="preview-link" aria-hidden="true"><i/><i/><i/></div>
     <div className="preview-node preview-node-gate">
      <span className="preview-node-cap">证据核验</span>
      <span className="preview-gate">
       <i className="preview-gate-pass">准入</i>
       <i className="preview-gate-scan"/>
       <i className="preview-gate-reject">拦截</i>
      </span>
      <span className="preview-hint">未准入不进入判定</span>
     </div>
     <div className="preview-link" aria-hidden="true"><i/><i/><i/></div>
     <div className="preview-node preview-node-out">
      <span className="preview-node-cap">四态判定</span>
      <span className="preview-states">
       <i className="preview-state is-fc">FC</i>
       <i className="preview-state is-pc">PC</i>
       <i className="preview-state is-unknown">UNKNOWN</i>
       <i className="preview-state is-nc">NC</i>
      </span>
      <span className="preview-hint">UNKNOWN 不出正式口径</span>
     </div>
    </div>
    <div className="preview-bar">
     <p className="preview-note">合成示例 · 不代表真实任务或处理速度</p>
     <button className="preview-play" onClick={()=>onNavigate('docs')} aria-label="查看使用流程"><i aria-hidden="true"/>查看使用流程</button>
    </div>
   </section>
   <footer className="hero-foot">
    <p className="home-disclosure">资料与索引在本地管理，生成所需文本发送至你选择的模型接口。</p>
   </footer>
  </section>

  <section className="home-values" id="laura-process">
   <p className="home-kicker">EVIDENCE / CONTROL / IMPROVEMENT</p>
   <h2>每一步都留有依据。</h2>
   <div>
    {[[DatabaseOutlined,'专属知识，数据可控','构建个人项目知识库，本地管理资料与索引，结合语义与关键词检索查找依据，模型调用由你选择。'],[FileDoneOutlined,'单条批量，高效应答','从一条要求到整份 Excel，生成附带来源的应答；查阅已审核历史，减少重复查找与整理。'],[ExperimentOutlined,'过程可查，持续改进','全链观测、评测集构建与使用指南，让每次应答可追溯，让改进有据可循。']].map(([Icon,title,text])=>{
     const Mark=Icon as typeof DatabaseOutlined;
     return <article key={String(title)}><Mark/><h3>{String(title)}</h3><p>{String(text)}</p></article>;
    })}
    <article>
     <FileSearchOutlined aria-hidden="true"/>
     <h3>拆解翻译，清晰起步</h3>
     <p>让读懂标书，成为清晰的起点。拆解与翻译使用独立任务，不混入应标判定和正式回填。</p>
     <div className="home-highlight-actions">
      <Button type="link" size="small" icon={<FileSearchOutlined aria-hidden="true"/>} onClick={()=>onNavigate('tender-analysis')}>标书拆解</Button>
      <Button type="link" size="small" icon={<TranslationOutlined aria-hidden="true"/>} onClick={()=>onNavigate('translation')}>标书翻译</Button>
     </div>
    </article>
   </div>
  </section>
  <footer className="home-footer">
   <HomeContact/>
   <small>历史应答只供查阅。页面演示和软件测试不代表业务准确率或量化节省收益。</small>
  </footer>
 </div>;
}
