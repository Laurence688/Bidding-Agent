import {useEffect,useState} from 'react';
const formatter=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
export default function BeijingClock(){const [now,setNow]=useState(()=>new Date());useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer);},[]);return <time className="beijing-clock" dateTime={now.toISOString()} aria-label="北京时间">北京时间 {formatter.format(now)}</time>;}
