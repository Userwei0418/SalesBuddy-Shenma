import {useEffect,useRef,useState} from 'react';
import {identity} from '../pc/data.js';

// Only one status read is in flight. Navigation/identity changes stop subsequent
// reads and discard late results, while transient errors require an explicit retry.
// The counters are diagnostic only: a long-running job is never failed by the
// browser and the hook never starts a second POST.
export function usePollingResource({id,owner,load,pending,refresh=0,delay=1500}) {
  const [state,setState]=useState({key:null,value:null,loading:false,error:null,pollCount:0,startedAt:null,lastPolledAt:null});
  const readers=useRef({load,pending});readers.current={load,pending};
  const key=JSON.stringify([id,owner,refresh]);
  useEffect(()=>{
    if(!id){setState({key,value:null,loading:false,error:null,pollCount:0,startedAt:null,lastPolledAt:null});return;}
    let active=true,timer,pollCount=0;
    const startedAt=Date.now();
    const current=()=>active && owner===identity();
    setState({key,value:null,loading:true,error:null,pollCount:0,startedAt,lastPolledAt:null});
    const read=async()=>{
      try{
        const value=await readers.current.load(id);
        if(!current())return;
        pollCount+=1;
        const lastPolledAt=Date.now();
        setState({key,value,loading:false,error:null,pollCount,startedAt,lastPolledAt,elapsedMs:lastPolledAt-startedAt});
        if(readers.current.pending(value))timer=setTimeout(read,delay);
      }catch(error){
        if(current())setState(previous=>({...previous,key,value:null,loading:false,error,pollCount,startedAt,lastPolledAt:Date.now(),elapsedMs:Date.now()-startedAt}));
      }
    };
    read();return()=>{active=false;clearTimeout(timer);};
  },[key,delay]);
  return state.key===key?state:{value:null,loading:Boolean(id),error:null,pollCount:0,startedAt:null,lastPolledAt:null,elapsedMs:0};
}
