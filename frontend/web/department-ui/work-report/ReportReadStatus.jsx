import React,{useEffect,useMemo,useState} from 'react';
import {Spin} from 'antd';
import {REPORT_SLOW_READ_AFTER} from './read-policy.js';

// useAsync's reload function identifies the current scope/identity request.
// A new request hides the old hint during render, before effect cleanup.
export default function ReportReadStatus({loading,requestKey,label}) {
  const read=useMemo(()=>({}),[loading,requestKey]);
  const [slowRead,setSlowRead]=useState(null);
  useEffect(()=>{
    if(!loading)return;
    let active=true;
    const timer=setTimeout(()=>{if(active)setSlowRead(read);},REPORT_SLOW_READ_AFTER);
    return()=>{active=false;clearTimeout(timer);};
  },[loading,read]);
  if(!loading)return null;
  const slow=slowRead===read;
  if(!label && !slow)return null;
  return <div className={label?'rv-loading':'rv-subtle rv-table-note'} role="status">
    {label && <Spin/>}<span>{slow?'数据较多，正在继续读取，请稍候':label}</span>
  </div>;
}
