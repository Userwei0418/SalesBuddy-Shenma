import {useCallback, useEffect, useRef, useState} from 'react';
import {identity} from '../../pc/data.js';
import {loadTaskAlignmentCapabilities, loadTaskKeyResults, loadTaskMetrics, loadTaskTargets} from './api.js';

const today = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const blankList = {owner:'',status:'loading',items:[],error:'',loaded:0,total:null};
export function useTaskAlignmentDirectory(enabled = true) {
  const owner = identity(), sequence = useRef(0), targetSequence = useRef(0);
  const [nonce, setNonce] = useState(0), [state, setState] = useState({owner:'',status:'loading',metrics:[],error:''});
  const [krState, setKrState] = useState(blankList), [targetState, setTargetState] = useState(blankList);
  const [queryState, setQueryState] = useState({owner,query:{period_type:'quarter',anchor_date:today()}});
  const targetQuery = queryState.owner === owner ? queryState.query : {period_type:'quarter',anchor_date:today()};
  const queryKey = JSON.stringify(targetQuery), [buckets, setBuckets] = useState({owner:'',values:{}});
  useEffect(() => {
    const serial = ++sequence.current, current = () => serial === sequence.current && owner === identity();
    if (!enabled) return () => {sequence.current++;};
    setState({owner,status:'loading',metrics:[],error:''});setKrState({...blankList,owner});
    Promise.all([loadTaskAlignmentCapabilities(), loadTaskMetrics()]).then(([,metrics]) => {
      if (!current()) return;
      setState({owner,status:'ready',metrics,error:''});
      loadTaskKeyResults({current,onProgress:progress => {if(current())setKrState(value=>({...value,...progress}));}}).then(items => {
        if(current() && items)setKrState(value=>({...value,owner,status:'ready',items,error:''}));
      }).catch(error => {if(current())setKrState({...blankList,owner,status:'error',error:error.message||'KR 目录暂时无法读取'});});
    }).catch(error => {
      if(current())setState({owner,status:[404,405,501].includes(error.statusCode)?'unavailable':'error',metrics:[],error:error.message||'任务对齐能力暂时无法读取'});
    });
    return () => {sequence.current++;};
  }, [owner,enabled,nonce]);
  useEffect(() => {
    const serial = ++targetSequence.current, current = () => serial === targetSequence.current && owner === identity();
    if(!enabled || state.owner !== owner || state.status !== 'ready')return () => {targetSequence.current++;};
    setTargetState({...blankList,owner});
    loadTaskTargets(targetQuery,{current,onProgress:progress=>{if(current())setTargetState(value=>({...value,...progress}));}}).then(items=>{
      if(!current() || !items)return;
      setTargetState(value=>({...value,owner,status:'ready',items,error:''}));
      // Retain explicit selections from previously browsed periods. Never derive a
      // period from due_at, nor turn a KR's effective_target into a direct target.
      setBuckets(value=>{
        const values = {...(value.owner===owner?value.values:{})};
        delete values[queryKey]; // Fresh reads win when two anchors refer to the same real period.
        return {owner,values:{...values,[queryKey]:items}};
      });
    }).catch(error=>{if(current())setTargetState({...blankList,owner,status:'error',error:error.message||'业绩目标暂时无法读取'});});
    return () => {targetSequence.current++;};
  }, [owner,enabled,state.owner,state.status,queryKey,nonce]);
  const merged = new Map();
  if(buckets.owner===owner)for(const list of Object.values(buckets.values))for(const row of list)if(row.id)merged.set(row.id,row);
  return {...(state.owner===owner?state:{owner,status:'loading',metrics:[],error:''}),
    keyResults:krState.owner===owner?krState.items:[],krStatus:krState.owner===owner?krState.status:'loading',krError:krState.owner===owner?krState.error:'',krLoaded:krState.owner===owner?krState.loaded:0,krTotal:krState.owner===owner?krState.total:null,
    targets:[...merged.values()],targetRows:targetState.owner===owner?targetState.items:[],targetStatus:targetState.owner===owner?targetState.status:'loading',targetError:targetState.owner===owner?targetState.error:'',targetLoaded:targetState.owner===owner?targetState.loaded:0,targetTotal:targetState.owner===owner?targetState.total:null,
    targetQuery,setTargetQuery:query=>setQueryState({owner,query}),owner,reload:useCallback(()=>setNonce(value=>value+1),[])};
}
