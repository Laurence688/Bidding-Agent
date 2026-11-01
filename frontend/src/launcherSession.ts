// Persistent sockets stay connected in background tabs; no heartbeat timers.
export function startLauncherSession(){
 let active=true,generation=0,socket:WebSocket|null=null,timer:number|undefined;
 const later=()=>{if(active)timer=window.setTimeout(connect,3000);};
 async function connect(){
  const round=generation;
  try{
   const response=await fetch('/api/v1/bootstrap',{cache:'no-store'});
   if(!response.ok)throw new Error('bootstrap unavailable');
   const {launcher_session:token}=await response.json();
   if(!active||round!==generation)return;
   if(!token){later();return;}
   const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/launcher/session`);
   socket=ws;
   ws.onopen=()=>{if(!active||round!==generation){ws.close();return;}ws.send(JSON.stringify({token}));};
   ws.onclose=()=>{if(socket===ws)socket=null;if(active&&round===generation)later();};
   ws.onerror=()=>ws.close();
  }catch{if(active&&round===generation)later();}
 }
 const hide=()=>{active=false;generation++;window.clearTimeout(timer);socket?.close(1000);socket=null;};
 const show=()=>{if(!active){active=true;void connect();}};
 window.addEventListener('pagehide',hide);
 window.addEventListener('pageshow',show);
 void connect();
 return ()=>{hide();window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show);};
}
