import {useEffect,useState,type RefObject} from 'react';
import {Icon} from './Icon';

export default function BackToTop({targetRef,inline=false,threshold=320,nearBottom=true}:{targetRef?:RefObject<HTMLElement|null>;inline?:boolean;threshold?:number;nearBottom?:boolean}){
 const [visible,setVisible]=useState(false);
 useEffect(()=>{
  const target=targetRef?.current;
  const update=()=>{
   const top=target?target.scrollTop:window.scrollY;
   const remaining=target?target.scrollHeight-target.clientHeight-top:document.documentElement.scrollHeight-window.innerHeight-top;
   setVisible(top>threshold||(nearBottom&&top>80&&remaining<40));
  };
  const scrollTarget=target??window;
  scrollTarget.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update);update();
  return()=>{scrollTarget.removeEventListener('scroll',update);window.removeEventListener('resize',update);};
 },[targetRef,threshold,nearBottom]);
 if(!visible)return null;
 function returnToTop(){
  const behavior=window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth';
  (targetRef?.current??window).scrollTo({top:0,behavior});
  const focusTarget=targetRef?.current?.querySelector<HTMLButtonElement>('[aria-label="关闭条目详情"]')??document.getElementById('main-content');
  focusTarget?.focus({preventScroll:true});
 }
 return <button className={inline?'back-to-top-inline':'back-to-top'} aria-label="返回顶部" title="返回顶部" onClick={returnToTop}><Icon name="arrow-up" size={18}/><span>返回顶部</span></button>;
}
