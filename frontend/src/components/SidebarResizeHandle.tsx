import {useEffect,useRef,useState} from 'react';

export default function SidebarResizeHandle({width,limit,onChange,onReset}:{width:number;limit:number;onChange:(value:number)=>void;onReset:()=>void}){
 const drag=useRef<{x:number;width:number}|null>(null),[dragging,setDragging]=useState(false);
 useEffect(()=>{if(!dragging)return;const {cursor,userSelect}=document.body.style;document.body.style.cursor='col-resize';document.body.style.userSelect='none';return()=>{document.body.style.cursor=cursor;document.body.style.userSelect=userSelect;};},[dragging]);
 const end=()=>{drag.current=null;setDragging(false);};
 return <div className={`sidebar-resize-handle${dragging?' dragging':''}`} role="separator" tabIndex={0} aria-label="调整侧栏宽度" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={limit} aria-valuenow={width} title="拖动调整宽度，双击恢复默认；方向键微调"
  onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();drag.current={x:e.clientX,width};setDragging(true);e.currentTarget.setPointerCapture(e.pointerId);}}
  onPointerMove={e=>{if(drag.current)onChange(drag.current.width+e.clientX-drag.current.x);}} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onDoubleClick={onReset}
  onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();onChange(width+(e.key==='ArrowRight'?16:-16));}else if(e.key==='Home'||e.key==='End'){e.preventDefault();onChange(e.key==='Home'?200:limit);}}}><span/></div>;
}
