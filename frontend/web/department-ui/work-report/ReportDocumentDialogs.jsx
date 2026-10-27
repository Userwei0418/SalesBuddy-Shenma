import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {App,Button,Checkbox,Select} from 'antd';
import {identity} from '../pc/data.js';
import {useAsync} from '../pc/ui.jsx';
import {HistoryDrawer,ExportDialog,ShareDialog} from './ReportDialogs.jsx';
import {dateLabel,revisionText} from './presentation.js';
import {accessLost} from './composition.js';
import {sharingAudience,checkedSharePreview,shareConfirmation,shareErrorText,shareSelectionFailure} from './sharing.js';
import {stable,safeFilename} from './state.js';

export function DocumentShare({document,service,owner,saving,error,onClose,onSave,onRevoke,onAccessLost,onRecheck}) {
  const [audience,setAudience]=useState(document.audience || {kind:'members',member_ids:[]});
  const [confirmedHash,setConfirmedHash]=useState('');
  const options=useAsync(async signal=>{try{return await service.options();}catch(error){if(!signal.aborted && owner===identity() && accessLost(error))onAccessLost(error);throw error;}},[owner,document.id,service]);
  const targets=options.value?.share_targets;
  let allowed;try{allowed=sharingAudience(audience,targets);}catch(_){}
  const selectionKey=stable(allowed || null);
  const preview=useAsync(async signal=>{
    if(!allowed || !options.value?.can_share)return null;
    try{return checkedSharePreview(await service.sharePreview(document.id,{version_no:document.version_no,audience:allowed},{signal}),document.version_no);}
    catch(failure){
      if(!signal.aborted && owner===identity() && accessLost(failure) && !shareSelectionFailure(failure))onAccessLost(failure);
      throw Error(failure.timeout?'共享权限核对超时，请重新核对':failure.outcomeUnknown?'共享权限核对暂未完成，请重新核对':shareErrorText(failure));
    }
  },[owner,document.id,document.version_no,service,selectionKey,options.value]);
  const choose=value=>{setConfirmedHash('');onRecheck?.();setAudience(value);};
  const recheck=()=>{setConfirmedHash('');onRecheck?.();preview.reload();};
  const confirmation=shareConfirmation(preview.value,confirmedHash);
  const blocked=options.loading || saving || Boolean(options.error);
  if(owner!==identity())return null;
  return <ShareDialog open onClose={onClose} loading={options.loading} saving={saving} error={options.error || error}
    alreadyShared={document.status==='shared'} canSubmit={Boolean(allowed && options.value?.can_share && !preview.loading && !preview.error && confirmation)}
    onSave={()=>allowed && confirmation && onSave(allowed,confirmation)} onRevoke={options.value?.can_share?onRevoke:undefined}
    selector={<div className="wr-field"><Select aria-label="共享范围" value={audience.kind} disabled={blocked}
      options={(targets?.allowed_kinds || []).map(value=>({value,label:{members:'指定成员',team:'指定部门',company:'全公司'}[value]}))} onChange={kind=>choose({kind,member_ids:[]})}/>
      {audience.kind==='members' && <Select aria-label="共享给成员" mode="multiple" value={audience.member_ids} disabled={blocked} placeholder="选择成员" optionFilterProp="label"
        options={(targets?.members || []).map(item=>({value:item.id,label:item.name}))} onChange={member_ids=>choose({kind:'members',member_ids})}/>}
      {audience.kind==='team' && <Select aria-label="共享给部门" value={audience.team_id} disabled={blocked} placeholder="选择部门" optionFilterProp="label"
        options={(targets?.teams || []).map(item=>({value:item.id,label:item.name}))} onChange={team_id=>choose({kind:'team',team_id})}/>}
      {allowed && <div className="wr-share-preview" aria-live="polite">
        {preview.loading?<p>正在核对接收人的查看权限…</p>:preview.error?<p>{preview.error}</p>:preview.value && <>
          <p><strong>可查看 {preview.value.readable_count} 人</strong> · 不可查看 {preview.value.unreadable_count} 人</p>
          {!preview.value.can_share && <p>当前没有可查看此报告的接收人，请调整范围。</p>}
          {preview.value.confirmation_required && <Checkbox checked={confirmedHash===preview.value.preview_hash} disabled={saving || Boolean(error)}
            onChange={event=>setConfirmedHash(event.target.checked?preview.value.preview_hash:'')}>我已知晓，{preview.value.unreadable_count} 人因数据权限不足暂时无法查看，仍共享给所选范围</Checkbox>}
          <p className="wr-muted">共享不会增加来源数据权限；接收人查看时仍按当时权限校验。</p>
        </>}
        {(preview.error || error || preview.value) && <Button size="small" onClick={recheck} disabled={saving || preview.loading}>重新核对</Button>}
      </div>}
    </div>}/>;
}

function ExportState({document,service,owner,onClose,onAccessLost}) {
  const {message}=App.useApp();
  const [state,setState]=useState({loading:true,error:null,ready:false});
  const active=useRef(false),reading=useRef(false),serial=useRef(0);
  const current=request=>active.current && request===serial.current && owner===identity();
  const read=async(action)=>{
    if(!active.current || reading.current || owner!==identity())return;
    reading.current=true;const request=++serial.current;setState({loading:true,error:null,ready:false});
    try {
      // Opening only checks availability. Every explicit copy/download obtains
      // a freshly authorized body; no export body is retained in React state.
      const value=await service.export(document.id,document.revision.id,'markdown');
      if(!current(request))return;
      if(value.document_id!==document.id || value.revision_id!==document.revision.id || typeof value.text!=='string')throw Error('导出内容与当前修订不一致，请重试');
      if(action==='copy') {
        try{await navigator.clipboard.writeText(value.text);}catch(_){throw Error('复制失败，请重试或使用下载');}
        if(current(request))message.success('报告正文已复制');
      } else if(action==='download') {
        const url=URL.createObjectURL(new Blob([value.text],{type:value.mime_type}));
        const link=window.document.createElement('a');link.href=url;link.download=safeFilename(value.filename);link.click();setTimeout(()=>URL.revokeObjectURL(url),0);
      }
      if(current(request))setState({loading:false,error:null,ready:true});
    } catch(error) {
      if(current(request)){setState({loading:false,error:error.message || '导出失败，请重试',ready:false});if(accessLost(error))onAccessLost(error);}
    } finally {if(request===serial.current)reading.current=false;}
  };
  useLayoutEffect(()=>{active.current=true;return()=>{active.current=false;serial.current++;reading.current=false;};},[]);
  useEffect(()=>{read();},[]);
  const close=()=>{active.current=false;serial.current++;onClose();};
  return <ExportDialog open onClose={close} loading={state.loading} error={state.error} onRetry={()=>read()} onCopy={()=>read('copy')} onDownload={()=>read('download')}
    value={state.ready?{title:document.revision.content.title,description:`导出当前可读的第 ${document.revision.revision_no} 版，保留人工编辑和用户补充标识。`}:null}/>;
}

export function DocumentExport(props) {
  return <ExportState key={JSON.stringify([props.owner,props.document.id,props.document.revision.id])} {...props}/>;
}

export function DocumentHistory({document,service,owner,onClose,onAccessLost}) {
  const [list,setList]=useState({items:[],next:null,loading:true,error:null}),[selected,setSelected]=useState(null);
  const active=useRef(false),reading=useRef(false);
  const valid=()=>active.current && owner===identity();
  const read=async(offset=0)=>{
    if(!valid() || reading.current)return;reading.current=true;
    setList(previous=>({...previous,loading:true,error:null,...(!offset?{items:[],next:null}:{})}));
    try {
      const response=await service.revisions(document.id,{limit:40,offset});if(!valid())return;
      const previous=offset?list.items:[],seen=new Set(previous.map(item=>item.id));
      for(const item of response.items){if(seen.has(item.id))throw Error('历史版本列表已变化，请重新读取');seen.add(item.id);}
      setList({items:[...previous,...response.items],next:response.has_more?response.next_offset:null,loading:false,error:null});
    }catch(error){if(valid()){setList({items:[],next:null,loading:false,error:error.message});setSelected(null);if(accessLost(error))onAccessLost(error);}}
    finally{reading.current=false;}
  };
  useEffect(()=>{active.current=true;read();return()=>{active.current=false;};},[]);
  const detail=useAsync(async()=>{
    if(!selected)return null;
    try{return await service.revision(document.id,selected);}catch(error){if(accessLost(error))onAccessLost(error);throw error;}
  },[owner,document.id,selected,service]);
  if(owner!==identity())return null;
  const titles={manual:'人工编辑',generated:'生成草稿',rewrite:'局部重写',basic_summary:'基础汇总'};
  return <HistoryDrawer open onClose={onClose} items={list.items.map(item=>({id:item.id,label:`第 ${item.revision_no} 版 · ${titles[item.origin] || '已保存'} · ${dateLabel(item.created_at)}`}))}
    loading={list.loading} error={list.error} onRetry={()=>read()} hasMore={list.next!==null} onMore={()=>read(list.next)} selectedId={selected} onSelect={setSelected}
    detailLoading={Boolean(selected && detail.loading)} detailError={detail.error} detail={detail.value?{title:detail.value.content.title,label:`第 ${detail.value.revision_no} 版`,text:revisionText(detail.value.content,document.metrics)}:null}/>;
}
