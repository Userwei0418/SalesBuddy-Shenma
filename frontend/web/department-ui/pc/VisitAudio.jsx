import React,{useEffect,useRef,useState} from 'react';
import {Button,Slider} from 'antd';
import {api,identity,runtime} from './data.js';

export default function VisitAudio({visit}){
  const ref=useRef(null),[state,setState]=useState({currentText:'00:00',durationText:'00:00'});
  const owner=identity();
  useEffect(()=>{
    if(!visit?.audio)return;
    const player=runtime().require('utils/visitAudioPlayer').create({api:api(),platform:runtime().wx,visitId:visit.id,audio:visit.audio,identity,onChange:setState});
    ref.current=player;setState({currentText:'00:00',durationText:'00:00'});
    const close=()=>player.dispose();
    window.addEventListener('sales:session-ended',close);
    return()=>{ref.current=null;player.dispose();window.removeEventListener('sales:session-ended',close);};
  },[visit?.id,visit?.audio?.id,owner]);
  if(!visit?.audio)return null;
  return <section aria-label="我的录音"><h3>我的录音</h3><p className="pc-muted">仅本人可回听，不随跟进正文共享</p>
    <Button loading={state.loading} onClick={()=>ref.current?.toggle()}>{state.loading?`正在读取 ${state.downloadPercent||0}%`:state.playing?'暂停':'播放'}</Button>
    <Slider aria-label="录音进度" min={0} max={100} value={state.progress||0} disabled={!state.duration} onChangeComplete={value=>ref.current?.seek(value)}/>
    <small>{state.currentText} / {state.durationText}</small>{state.error&&<p role="alert" className="pc-error">{state.error}</p>}
  </section>;
}
