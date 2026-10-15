import React,{useEffect,useRef,useState} from 'react';
import {identity} from '../pc/data.js';
import {SourcesDrawer} from './ReportDialogs.jsx';
import {appendSources} from './state.js';
import {sourceViews,supplementView} from './presentation.js';
import {accessLost} from './composition.js';
import {workReportApi} from './api.js';

function SourcePages({snapshotId,sourceType,owner,onClose,onAccessLost,service}) {
  const [state,setState]=useState({items:[],loading:true,error:null,next:null});
  const current=useRef(false),reading=useRef(false),sequence=useRef(0);
  const read=async(offset=0)=>{
    if(reading.current || !current.current || owner!==identity())return;
    reading.current=true;const request=++sequence.current;
    setState(previous=>({...previous,loading:true,error:null,...(offset===0?{items:[],next:null}:{})}));
    try {
      const [page,snapshot]=await Promise.all([service.sources(snapshotId,{source_type:sourceType,limit:40,offset}),!offset && !sourceType?service.snapshot(snapshotId):null]);
      if(!current.current || owner!==identity() || request!==sequence.current)return;
      const shown={items:sourceViews(page.items),hasMore:page.has_more,nextOffset:page.next_offset};
      const supplements=(snapshot?.selection.supplements || []).map(supplementView);
      const items=appendSources(offset===0?supplements:state.items,shown);
      setState({items,loading:false,error:null,next:page.has_more?page.next_offset:null});
    } catch(error) {
      if(current.current && owner===identity() && request===sequence.current) {
        setState({items:[],loading:false,error:error.message || '材料暂时无法读取',next:null});
        if(accessLost(error))onAccessLost?.(error);
      }
    } finally {reading.current=false;}
  };
  useEffect(()=>{current.current=true;read();return()=>{current.current=false;sequence.current++;};},[]);
  if(owner!==identity())return null;
  return <SourcesDrawer open onClose={onClose} items={state.items} loading={state.loading} error={state.error}
    onRetry={()=>read()} hasMore={state.next!==null} onLoadMore={()=>read(state.next)}/>;
}

export default function ReportSources({open,service=workReportApi,...props}) {
  return open?<SourcePages key={JSON.stringify([props.owner,props.snapshotId,props.sourceType])} {...props} service={service}/>:null;
}
