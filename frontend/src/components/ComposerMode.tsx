import {useState} from 'react';
import {Button,Dropdown} from 'antd';
import {MessageOutlined,FileDoneOutlined,TranslationOutlined,DownOutlined,CheckOutlined} from '@ant-design/icons';

const modes=[{key:'PRODUCT_QA',label:'问答',icon:<MessageOutlined aria-hidden="true"/>},{key:'SOC',label:'应标',icon:<FileDoneOutlined aria-hidden="true"/>},{key:'TRANSLATION',label:'翻译',icon:<TranslationOutlined aria-hidden="true"/>}];
export default function ComposerMode({mode,targetLanguage,onModeChange,onTargetLanguageChange,disabled=false}:{mode:string;targetLanguage:string;onModeChange:(mode:string)=>void;onTargetLanguageChange:(language:string)=>void;disabled?:boolean}){
 const [open,setOpen]=useState(false),selected=modes.find(item=>item.key===mode)??modes[0];
 const items:any[]=modes.map(item=>({...item,label:<span className="composer-mode-option">{item.label}{item.key===mode&&<CheckOutlined aria-hidden="true"/>}</span>}));
 if(mode==='TRANSLATION')items.push({type:'divider'},...['zh','en'].map(key=>({key,label:key==='zh'?'英译中':'中译英',icon:targetLanguage===key?<CheckOutlined aria-hidden="true"/>:<span className="mode-check-space"/>})));
 return <Dropdown trigger={['click']} open={open&&!disabled} onOpenChange={(next,info)=>{if(info.source==='trigger')setOpen(next);}} menu={{items,selectedKeys:[mode,...(mode==='TRANSLATION'?[targetLanguage]:[])],onClick:({key})=>{
  if(key==='zh'||key==='en'){onTargetLanguageChange(key);setOpen(false);}else{if(key!==mode)onModeChange(key);setOpen(key==='TRANSLATION');}
 }}}><Button className="composer-mode" type="text" disabled={disabled} aria-label={`任务模式：${selected.label}`} aria-haspopup="menu" aria-expanded={open&&!disabled} title={mode==='TRANSLATION'?`翻译 · ${targetLanguage==='en'?'中译英':'英译中'}`:selected.label}>{selected.icon}<span>{selected.label}</span><DownOutlined aria-hidden="true" className="mode-chevron"/></Button></Dropdown>;
}
