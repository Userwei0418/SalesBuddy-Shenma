const {UUID}=require('./visitUploadIntent');
const EMPTY={loading:false,playing:false,current:0,duration:0,progress:0,downloadPercent:0,error:''};
function metadata(value,visitId){
  if(!value || !UUID.test(visitId||'') || !UUID.test(value.id||'') || value.playback_path!==`/visits/${visitId}/audio`)return null;
  const type=String(value.mime_type||'').split(';')[0];
  if(!/^audio\/[a-z0-9.+-]+$/i.test(type)||!Number.isSafeInteger(value.size_bytes)||value.size_bytes<=0||value.size_bytes>100*1024*1024)return null;
  return {id:value.id,playback_path:value.playback_path,mime_type:type,size_bytes:value.size_bytes,filename:String(value.filename||'本次录音')};
}
function time(seconds){const value=Math.max(0,Math.floor(Number(seconds)||0));return `${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;}
function create({api,platform,visitId,audio,identity,onChange}){
  const owner=identity(),meta=metadata(audio,visitId);
  let disposed=false,resource='',blob=false,player=null,state={...EMPTY},timer;
  const current=()=>!disposed&&identity()===owner;
  const emit=patch=>{state={...state,...patch};if(current())onChange({...state,currentText:time(state.current),durationText:time(state.duration)});};
  const remove=src=>{if(!src)return;if(blob){URL.revokeObjectURL(src);}else{try{platform.getFileSystemManager().unlink({filePath:src});}catch(_){}}};
  function dispose(){if(disposed)return;disposed=true;clearInterval(timer);if(player){try{player.stop();player.destroy();}catch(_){}player=null;}remove(resource);resource='';}
  timer=setInterval(()=>{if(!current())dispose();},250);
  async function load(){
    if(!current()||state.loading)return false;
    if(!meta){emit({error:'本次录音暂不可回听'});return false;}
    emit({loading:true,error:''});
    try{
      const result=await api.readVisitAudio(visitId,{isCancelled:()=>!current(),onProgress:({received,total})=>emit({downloadPercent:total>0?Math.min(100,Math.floor(received/total*100)):0})});
      if(!current()){if(result.tempFilePath)remove(result.tempFilePath);return false;}
      if(result.tempFilePath){
        resource=result.tempFilePath;
        const info=await new Promise((resolve,reject)=>platform.getFileSystemManager().getFileInfo({filePath:resource,success:resolve,fail:()=>reject(Error('录音临时文件不可读取，请重试'))}));
        if(info.size!==meta.size_bytes)throw Error('录音内容已变化，请重新打开跟进记录');
      }else{
        if(!result.data||result.data.byteLength!==meta.size_bytes)throw Error('录音内容已变化，请重新打开跟进记录');
        blob=true;resource=URL.createObjectURL(new Blob([result.data],{type:meta.mime_type}));
      }
      if(!current()){remove(resource);resource='';return false;}
      player=platform.createInnerAudioContext();
      player.onPlay(()=>emit({playing:true,error:''}));player.onPause(()=>emit({playing:false}));
      player.onEnded(()=>emit({playing:false,current:0,progress:0}));
      player.onError(()=>emit({playing:false,error:'暂时无法播放录音，请重新点击播放或重新打开记录'}));
      const update=()=>{const duration=Number.isFinite(player.duration)?player.duration:0,currentTime=Number(player.currentTime)||0;emit({duration,current:currentTime,progress:duration?Math.min(100,currentTime/duration*100):0});};
      player.onTimeUpdate(update);player.onCanplay(update);player.src=resource;
      emit({loading:false,downloadPercent:100});return true;
    }catch(error){remove(resource);resource='';if(current())emit({loading:false,playing:false,error:error.message||'录音读取失败，请重试'});return false;}
  }
  return {dispose,async toggle(){if(!current()){dispose();return;}if(state.loading)return;if(!player && !await load())return;if(!current()){dispose();return;}state.playing?player.pause():player.play();},
    seek(percent){if(current()&&player&&state.duration>0)player.seek(Math.max(0,Math.min(100,Number(percent)||0))*state.duration/100);},pause(){if(player)player.pause();},
  };
}
module.exports={metadata,create,EMPTY,time};
