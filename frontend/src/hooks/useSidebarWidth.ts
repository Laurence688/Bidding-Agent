import {useEffect,useState} from 'react';

const DEFAULT=216,MIN=200,KEY='laura-sidebar-width';
const maximum=()=>window.innerWidth<=700?420:Math.max(MIN,Math.min(420,Math.floor(window.innerWidth*.45)));
export function useSidebarWidth(){
 const [limit,setLimit]=useState(maximum);
 const [width,setWidth]=useState(()=>{try{const raw=localStorage.getItem(KEY),saved=raw===null?DEFAULT:Number(raw);return Number.isFinite(saved)?Math.max(MIN,Math.min(420,saved)):DEFAULT;}catch{return DEFAULT;}});
 useEffect(()=>{const resize=()=>{const next=maximum();setLimit(next);};window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[]);
 useEffect(()=>{try{localStorage.setItem(KEY,String(width));}catch{/* 本机禁用存储时仍允许调整。 */}},[width]);
 return {width:Math.min(width,limit),limit,setWidth:(next:number)=>setWidth(Math.round(Math.max(MIN,Math.min(limit,next)))),reset:()=>setWidth(Math.min(DEFAULT,limit))};
}
