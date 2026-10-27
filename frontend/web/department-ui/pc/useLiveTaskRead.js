import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {identity} from './data.js';

// Small task projections poll only while visible. Preserve confirmed rows during
// transient failures; identity/scope changes and authorization failures clear them.
export function useLiveTaskRead(loader,deps,{enabled=true}={}){
 const owner=identity(),request=useMemo(()=>({owner,loader}),[owner,...deps]);
 const [state,setState]=useState({request:null,value:null,loading:true,error:'',lastSyncedAt:null}),retry=useRef(()=>{});
 useEffect(()=>{
  let active=true,flight=false,denied=false;
  const current=()=>active&&request.owner===identity();
  const run=async()=>{
   if(flight||denied||document.hidden)return;
   if(navigator.onLine===false){if(current())setState(old=>({...old,request,loading:false,error:'当前离线，请恢复网络后重试',value:old.request===request?old.value:null}));return;}
   flight=true;
   try{const value=await request.loader();if(current())setState({request,value,loading:false,error:'',lastSyncedAt:Date.now()});}
   catch(error){if(current()){denied=[401,403].includes(error.statusCode);setState(old=>({request,value:denied?null:old.request===request?old.value:null,loading:false,error:error.message||'任务同步失败，请重试',lastSyncedAt:old.request===request?old.lastSyncedAt:null}));}}
   finally{flight=false;}
  };
  retry.current=()=>{denied=false;return run();};run();
  const wake=()=>{if(enabled)run();},timer=enabled?setInterval(wake,3000):null;
  document.addEventListener('visibilitychange',wake);globalThis.addEventListener('online',wake);
  return()=>{active=false;clearInterval(timer);document.removeEventListener('visibilitychange',wake);globalThis.removeEventListener('online',wake);};
 },[request,enabled]);
 return {...(state.request===request?state:{value:null,loading:true,error:'',lastSyncedAt:null}),reload:useCallback(()=>retry.current(),[])};
}
