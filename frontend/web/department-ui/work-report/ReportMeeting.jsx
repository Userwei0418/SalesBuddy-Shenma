import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,Input,Select,Spin,Tag} from 'antd';
import {identity} from '../pc/data.js';
import {todayKey} from '../pc/period.js';
import {useAsync} from '../pc/ui.jsx';
import FilterBar from '../pc/FilterBar.jsx';
import {PERIOD_CHOICES,calendarHint} from './state.js';
import {fromPeriod,toPeriod} from './presentation.js';
import {accessLost} from './composition.js';
import {usePollingResource} from './usePollingResource.js';
import {meetingOptions,meetingScopes,meetingSelection,matchingMeetingSnapshot,meetingDraft} from './meeting-selection.js';
import MeetingFactsBoard from './MeetingFactsBoard.jsx';

function MeetingState({owner,service,options,initialPeriod,onDocument,onCompose,pollDelay=1500}) {
  const [filters,setFilters]=useState({period:initialPeriod || fromPeriod(options.default_period),anchor:todayKey(),scopeKey:meetingScopes(options)[0]?.value});
  const [prepared,setPrepared]=useState(null),[error,setError]=useState(null),[busy,setBusy]=useState(null),[refresh,setRefresh]=useState(0);
  const active=useRef(false),serial=useRef(0),writing=useRef(false);
  useEffect(()=>{active.current=true;return()=>{active.current=false;serial.current++;};},[]);
  const current=()=>active.current && owner===identity();
  const snapshot=usePollingResource({id:prepared?.id,owner,
    load:async id=>matchingMeetingSnapshot(await service.meetingSnapshot(id),prepared.selection),pending:value=>value.status==='preparing',refresh,delay:pollDelay});
  const change=patch=>{if(writing.current || !current())return;serial.current++;setFilters(value=>({...value,...patch}));setPrepared(null);setError(null);};
  const mutate=async(kind,action,accept)=>{
    if(writing.current || !current())return;
    writing.current=true;setBusy(kind);setError(null);const request=++serial.current;
    try{const value=await action();if(current() && request===serial.current)accept(value);}
    catch(failure){if(current() && request===serial.current){setError(failure.message || '操作未完成，请重试');if(accessLost(failure))setPrepared(null);}}
    finally{writing.current=false;if(current() && request===serial.current)setBusy(null);}
  };
  const prepare=()=>{
    let selection;try{selection=meetingSelection(filters,options);}catch(failure){setError(failure.message);return;}
    setPrepared(null);
    mutate('prepare',async()=>matchingMeetingSnapshot(await service.prepareMeeting(selection),selection),value=>setPrepared({id:value.id,selection}));
  };
  const ready=!snapshot.error && snapshot.value?.status==='ready';
  const save=()=>{
    let payload;try{payload=meetingDraft(snapshot.value,options);}catch(failure){setError(failure.message);return;}
    mutate('save',()=>service.saveMeeting(payload),value=>{
      if(!value?.id || !['private','draft'].includes(value.status)){setError('保存回执不完整，请到我的报告核对保存结果');return;}
      onDocument(value.id);
    });
  };
  const scheme=options.default_schemes.find(item=>item.period_kind===toPeriod(filters.period));
  const preparing=busy==='prepare' || snapshot.loading || snapshot.value?.status==='preparing';
  if(owner!==identity())return null;
  return <div className="wr-compose-flow">
    <FilterBar label="会议看板条件" moreLabel="报告条件" actions={<Button type="primary" disabled={Boolean(busy) || !options.can_prepare || preparing} loading={preparing} onClick={prepare}>{ready?'刷新本期看板':'生成看板'}</Button>}>
      <Select aria-label="报告周期" value={filters.period} options={PERIOD_CHOICES.filter(row=>options.default_schemes.some(item=>item.period_kind===toPeriod(row.value)))} onChange={period=>change({period})} disabled={Boolean(busy)} data-toolbar-width={120}/>
      <Input aria-label="周期内日期" type="date" value={filters.anchor} max={todayKey()} onChange={event=>change({anchor:event.target.value})} title={calendarHint(filters.period,filters.anchor)} disabled={Boolean(busy)} data-toolbar-width={156}/>
      <Select aria-label="生成范围" value={filters.scopeKey} options={meetingScopes(options)} onChange={scopeKey=>change({scopeKey})} disabled={Boolean(busy)} data-toolbar-width={220}/>
    </FilterBar>
    <div className="wr-change-template"><span>{calendarHint(filters.period,filters.anchor)} · {scheme?.name || '模板暂不可用'}</span><span>个人生成本人报告 · leader 可生成所管理团队报告</span></div>
    {!options.can_prepare && <Alert type="info" title="当前账号暂不可生成会议看板" description="可在我的报告中查看已获共享的报告。"/>}
    {error && <Alert type="error" title={error}/>}
    {snapshot.error && <Alert type="error" title={snapshot.error.message || '看板暂时无法读取'} action={<Button onClick={()=>setRefresh(value=>value+1)}>重新读取状态</Button>}/>}
    {snapshot.value?.status==='failed' && <Alert type="error" title={snapshot.value.error?.detail || '本次看板准备失败，可重新生成'}/>}
    {preparing && <div className="wr-panel wr-change-loading"><Spin/><div><b>正在整理本期变化</b><p className="wr-muted">核对认领、跟进、商机与金额历史，完成后展示本次统计。</p></div></div>}
    {ready && <MeetingFactsBoard key={snapshot.value.id} facts={snapshot.value.meeting_facts} title={`${scheme?.name || '本期经营变化'} · ${snapshot.value.scope.label}`} actions={<>
      <Tag>尚未保存</Tag>{filters.period==='week' && onCompose && <Button disabled={Boolean(busy)} onClick={()=>onCompose(filters)}>生成六节周报</Button>}
      <Button type="primary" onClick={save} loading={busy==='save'} disabled={Boolean(busy) || !options.can_save_draft}>保存个人草稿</Button>
    </>}/>}
    {!ready && !preparing && !snapshot.error && <section className="wr-paper wr-paper-empty" aria-label="会议看板待生成">
      <h2>本期做了什么，业务发生了哪些变化</h2><p>选择日期和范围，查看新增客户、跟进、商机与业绩变化。</p>
      <Button type="primary" disabled={!options.can_prepare || Boolean(busy)} onClick={prepare}>生成本期看板</Button>
      <p className="wr-muted">自动使用对应周期模板，保存后可编辑说明，再主动共享。</p>
    </section>}
  </div>;
}

export default function ReportMeeting(props) {
  const options=useAsync(async()=>meetingOptions(await props.service.meetingOptions()),[props.owner,props.service]);
  return options.loading?<Spin/>:options.error?<Alert type="error" title={options.error} action={<Button onClick={options.reload}>重新读取会议看板配置</Button>}/>:
    options.value?<MeetingState key={props.owner} {...props} options={options.value}/>:null;
}
