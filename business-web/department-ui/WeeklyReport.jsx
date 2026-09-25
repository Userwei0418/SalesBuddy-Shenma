import React, {useEffect, useRef, useState} from 'react';
import {App, Alert, Button, Collapse, Input, Select, Tag} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import WeeklyReportDemo from './WeeklyReportDemo.jsx';
import {uploadDate} from './weekly-report-model.mjs';
import './weekly-report.css';

const STATE = {queued:'等待生成',running:'正在生成',succeeded:'已完成',failed:'生成失败',cancelled:'已取消'};
const ERRORS = {
  WEEKLY_CONTEXT_TOO_LARGE:'素材较多，已停止生成。请联系管理员调整处理容量，原始记录不会被截断。',
  WEEKLY_DRAFT_VERSION_CONFLICT:'这份周报已在其他页面修改。当前编辑已保留，请核对服务器版本后再保存。',
  WEEKLY_AGENT_NOT_CONFIGURED:'周报服务尚未启用，请联系管理员。',
  WEEKLY_SOURCE_SUBJECT_UNSUPPORTED:'部分记录未关联客户，暂时不能生成周报。请联系管理员核对关联，系统不会省略这些记录。',
  WEEKLY_SOURCE_ASSOCIATION_UNSUPPORTED:'部分历史记录关联多个业务对象，需要先核对周报归属；系统不会只选其中一项。',
  WEEKLY_INVALID_REPORT_WEEK:'报告周期无效，请刷新页面后重试。',
  WEEKLY_SOURCE_ACCESS_CHANGED:'来源记录已变更或当前无权读取，请联系管理员核对。',
  WEEKLY_ENTITY_ACCESS_INCOMPLETE:'部分关联客户或商机无法读取，请联系管理员核对权限和档案。',
  WEEKLY_SOURCE_ACCESS_INCOMPLETE:'部分本人记录无法读取，暂时不能生成完整周报。',
};
const explain = e => ERRORS[e?.message] || e?.message || '操作失败，请重试';
const active = r => r && ['queued','running'].includes(r.status);
const weekBefore = week => new Date(Date.parse(`${week}T12:00:00Z`)-7*86400000).toISOString().slice(0,10);
const read = key => {try{return JSON.parse(sessionStorage.getItem(key));}catch{return null;}};
function requestId(key) {
  const existing=read(key); if(typeof existing==='string') return existing;
  const id=crypto.randomUUID(); sessionStorage.setItem(key,JSON.stringify(id)); return id;
}
const pendingKey = (scope, week) => `sales-web:weekly-request:${scope}:${week}`;
function LiveReport({page, session}) {
  const {message,modal}=App.useApp();
  const scope=`${session.workspaceId}:${session.userId}:${session.role}:${session.permissionVersion}`;
  const [kind,setKind]=useState('recent'), [currentWeek,setCurrentWeek]=useState(null);
  const [meta,setMeta]=useState(null), [records,setRecords]=useState([]), [reports,setReports]=useState([]);
  const [report,setReport]=useState(null), [text,setText]=useState(''), [dirty,setDirty]=useState(false);
  const [loading,setLoading]=useState(true), [busy,setBusy]=useState(false), [error,setError]=useState(''), [sourceError,setSourceError]=useState('');
  const [conflict,setConflict]=useState(false), [query,setQuery]=useState('');
  const [historyMore,setHistoryMore]=useState(false);
  const lifetime=useRef(0), selection=useRef(0), reportRef=useRef(null), dirtyRef=useRef(false);
  const sourceJob=useRef(0);
  reportRef.current=report; dirtyRef.current=dirty;
  const api=(path='', options={})=>page.weeklyRequest(path,options);
  const week=kind==='previous' && currentWeek ? weekBefore(currentWeek) : currentWeek;
  const allowed=code=>session.permissions?.[code]===true;
  const alive=n=>lifetime.current===n && !page._destroyed;
  const draftKey=id=>`sales-web:weekly-edit:${scope}:${id}`;
  useEffect(()=>()=>{lifetime.current++;selection.current++;sourceJob.current++;},[]);
  async function sources(path, n=lifetime.current) {
    const serial=++sourceJob.current; let offset=0, rows=[], first;
    try {
      do {
        const result=await api(`${path}${path.includes('?')?'&':'?'}limit=50&offset=${offset}`);
        if(!alive(n)||serial!==sourceJob.current)return;
        first ||= result;
        if(result.total!==first.total)throw Error('素材已变化，请刷新后重试');
        rows.push(...result.items);
        if(!result.has_more)break;
        if(!Number.isInteger(result.next_offset)||result.next_offset<=offset)throw Error('素材分页异常，请刷新');
        offset=result.next_offset;
      } while(true);
      if(new Set(rows.map(r=>r.id)).size!==rows.length)throw Error('素材已变化，请刷新后重试');
      if(alive(n)&&serial===sourceJob.current){setMeta(first);setRecords(rows);setSourceError('');if(!currentWeek&&kind==='recent')setCurrentWeek(first.report_week);}
    } catch(e){if(alive(n)&&serial===sourceJob.current){setSourceError(explain(e));setRecords([]);}}
  }
  async function openReport(id, n=lifetime.current) {
    const serial=++selection.current;
    const value=await api(`/${id}`);
    if(!alive(n)||serial!==selection.current)return;
    setReport(value);reportRef.current=value;setReports(rows=>rows.map(r=>r.id===id?value:r));
    const buffered=read(draftKey(id));
    setText(buffered?.text ?? value.body_markdown ?? '');setDirty(!!buffered);
    setConflict(!!buffered&&buffered.version!==value.draft_version);
    await sources(`/${id}/sources`,n);
  }
  async function load() {
    const n=lifetime.current; setLoading(true);setError('');
    setReport(null);reportRef.current=null;setRecords([]);setMeta(null);setText('');setDirty(false);setConflict(false);selection.current++;
    try {
      const result=await api(`?limit=20${week?`&report_week=${week}`:''}`);
      if(!alive(n))return;
      // Server dates establish the current week on first load; avoid a client clock deciding scope.
      if(!currentWeek){
        setCurrentWeek(result.current_week);return;
      }
      setReports(result.items);setHistoryMore(result.has_more);
      if(result.items.length)await openReport(result.items[0].id,n);
      else if(allowed('weekly_report.generate'))await sources(`/sources?report_week=${week}`,n);
    }catch(e){if(alive(n))setError(explain(e));}
    finally{if(alive(n))setLoading(false);}
  }
  useEffect(()=>{if(allowed('weekly_report.read'))load();else setLoading(false);return()=>{lifetime.current++;selection.current++;sourceJob.current++;};},[week]);
  useEffect(()=>{
    if(!active(report))return;
    const n=lifetime.current,id=report.id; let stopped=false,timer;
    const poll=async()=>{
      try {
        const next=await api(`/${id}`);
        if(stopped||!alive(n)||reportRef.current?.id!==id)return;
        setReport(next);reportRef.current=next;setError('');
        if(!dirtyRef.current)setText(next.body_markdown||'');
        setReports(rows=>rows.map(r=>r.id===id?next:r));
        if(active(next))timer=setTimeout(poll,1800);
      }catch(e){if(!stopped&&alive(n)){setError(explain(e));timer=setTimeout(poll,5000);}}
    };
    timer=setTimeout(poll,1000);return()=>{stopped=true;clearTimeout(timer);};
  },[report?.id,report?.status]);
  function discard(action){
    if(!dirty)return action();
    modal.confirm({title:'当前修改尚未保存',content:'离开后仍可回到这份周报继续编辑。',okText:'暂存并切换',cancelText:'继续编辑',onOk:action});
  }
  async function generate(){
    if(dirty){message.info('请先保存当前修改，再生成新版本');return;}
    const n=lifetime.current,key=pendingKey(scope,week);setBusy(true);setError('');
    try {
      const value=await api('',{method:'POST',data:{request_id:requestId(key),report_week:week}});
      sessionStorage.removeItem(key);
      if(!alive(n))return;
      setReports(rows=>[value,...rows.filter(r=>r.id!==value.id)]);
      await openReport(value.id,n);
    }catch(e){
      if([400,403,404,409,422].includes(e.statusCode))sessionStorage.removeItem(key);
      if(alive(n))setError(explain(e));
    }finally{if(alive(n))setBusy(false);}
  }
  async function save(){
    const n=lifetime.current,id=report.id,version=report.draft_version,body=text;setBusy(true);setError('');
    try {
      const next=await api(`/${id}/draft`,{method:'PATCH',data:{expected_version:version,body_markdown:body}});
      sessionStorage.removeItem(draftKey(id));
      if(!alive(n)||reportRef.current?.id!==id)return;
      setReport(next);setDirty(false);setConflict(false);message.success('周报已保存');
    }catch(e){if(alive(n)){setError(explain(e));if(e.statusCode===409)setConflict(true);}}
    finally{if(alive(n))setBusy(false);}
  }
  async function cancel(){
    const n=lifetime.current,id=report.id;setBusy(true);
    try{const next=await api(`/${id}/cancel`,{method:'POST'});if(alive(n)&&reportRef.current?.id===id){setReport(next);setReports(rows=>rows.map(r=>r.id===id?next:r));}}
    catch(e){if(alive(n))setError(explain(e));}finally{if(alive(n))setBusy(false);}
  }
  async function moreHistory(){
    const n=lifetime.current;setBusy(true);
    try{const next=await api(`?report_week=${week}&limit=20&offset=${reports.length}`);if(alive(n)){setReports(rows=>[...rows,...next.items.filter(x=>!rows.some(r=>r.id===x.id))]);setHistoryMore(next.has_more);}}
    catch(e){if(alive(n))setError(explain(e));}finally{if(alive(n))setBusy(false);}
  }
  async function reloadServer(){
    const n=lifetime.current,id=report.id;setBusy(true);
    try{const latest=await api(`/${id}`);if(!alive(n)||reportRef.current?.id!==id)return;setReport(latest);setConflict(false);
      // Keep this tab's text; an explicit second save merges the user's chosen version.
      sessionStorage.setItem(draftKey(id),JSON.stringify({text,version:latest.draft_version}));
      modal.info({title:'服务器最新版本',content:<Input.TextArea aria-label="服务器最新正文" readOnly value={latest.body_markdown||''} autoSize={{minRows:6,maxRows:14}}/>,okText:'已核对，继续编辑本地内容'});
    }catch(e){if(alive(n))setError(explain(e));}finally{if(alive(n))setBusy(false);}
  }
  if(!allowed('weekly_report.read'))return <SbStatePanel state="empty" title="当前账号没有周报权限"/>;
  const visible=records.filter(r=>[r.customer_name,r.opportunity_name,r.follow_up_record,r.next_action].join(' ').toLowerCase().includes(query.toLowerCase().trim()));
  return <section className="ds-weekly" aria-label="周报">
    <div className="ds-weekly-heading"><div><h1>周报</h1><p className="ds-muted">按周查看 · 基于本人近 14 天上传的完整记录</p></div><div className="ds-weekly-period"><span>{session.userName} · 本人</span>
      <Select aria-label="周报周期" value={kind} disabled={loading||busy||!currentWeek} options={[{value:'recent',label:'本周（截至当前）'},{value:'previous',label:'上一个自然周'}]} onChange={v=>discard(()=>setKind(v))}/>
      <span>{meta?.report_week} — {meta?.report_week_end}</span></div></div>
    {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>discard(load)} disabled={busy}>重新读取</Button>}/>}
    <div className="ds-weekly-columns"><section className="ds-panel ds-weekly-records" aria-label="更新记录">
      <header className="ds-weekly-panel-head"><div><h2>更新记录 · {records.length}</h2><span className="ds-muted">{meta?.period.start_date} — {meta?.period.end_date}{report?' · 本次生成使用的素材':' · 待生成素材'}</span></div></header>
      {sourceError?<Alert type="error" title={sourceError}/>:loading?<SbStatePanel state="loading" title="正在读取周报"/>:!records.length?<SbStatePanel state="empty" title="这个周期暂无可用记录"/>:<>
      <Input.Search aria-label="搜索更新记录" placeholder="搜索客户、商机或原文" value={query} onChange={e=>setQuery(e.target.value)} allowClear/>
      <div className="ds-weekly-record-list">{visible.map(r=><article className="ds-weekly-record" key={r.id}><div className="ds-weekly-record-title"><div><strong>{r.customer_name||'客户名称未填写'}</strong><span>{r.opportunity_name||'未关联商机'}</span></div><time>{uploadDate(r)}</time></div><p className="ds-weekly-excerpt">{r.follow_up_record}</p><Collapse ghost items={[{key:'original',label:'查看完整记录',children:<><p style={{whiteSpace:'pre-wrap'}}>{r.follow_up_record}</p><p>下一步：{r.next_action||'未填写'}</p><p>跟进日期：{r.visit_date||'未填写'}</p></>}]} /></article>)}</div></>}
      <footer className="ds-weekly-generate"><Button aria-label={read(pendingKey(scope,week))?'重试生成请求':reports.length?'生成新版本':'生成周报'} type="primary" block size="large" disabled={!week||loading||busy||!!sourceError||active(report)||dirty||!allowed('weekly_report.generate')} loading={busy&&!active(report)} onClick={generate}>{read(pendingKey(scope,week))?'重试生成请求':reports.length?'生成新版本':'生成周报'}</Button><span className="ds-muted">完整读取对应 14 天素材，生成新版本会保留历史稿。</span></footer>
    </section><section className="ds-panel ds-weekly-output" aria-label="周报草稿">
      <header className="ds-weekly-panel-head"><div><h2>周报草稿 {report&&<Tag>{STATE[report.status]}</Tag>}</h2></div>
      <Button disabled={!text} onClick={async()=>{try{await navigator.clipboard.writeText(text);message.success('已复制');}catch{message.error('复制失败，请手动复制');}}}>复制周报</Button></header>
      {!!reports.length&&<Select aria-label="周报版本" value={report?.id} style={{width:'100%'}} disabled={busy||loading} options={reports.map(r=>({value:r.id,label:`${new Date(r.created_at).toLocaleString('zh-CN')} · ${STATE[r.status]}`}))} onChange={id=>discard(()=>openReport(id).catch(e=>setError(explain(e))))}/>}
      {historyMore&&<Button onClick={moreHistory} disabled={busy}>加载更早版本</Button>}
      {active(report)?<><SbStatePanel state="loading" title="正在整理周报" description="可以离开页面，稍后回来查看。"/>{allowed('weekly_report.cancel')&&<Button onClick={cancel} disabled={busy}>取消本次生成</Button>}</>:report?.status==='failed'?<Alert type="error" title="本次生成失败" description="历史周报仍然保留，可以重新生成。"/>:report?.status==='cancelled'?<SbStatePanel state="empty" title="本次生成已取消"/>:report?.result_status==='insufficient_data'?<SbStatePanel state="empty" title="素材不足，尚未生成正文"/>:report?.result_status==='invalid_input'?<Alert type="error" title="素材未通过校验，请核对后重试"/>:report?.result_status==='ready'?<div className="ds-weekly-paper"><h3>{report.title}</h3><p className="ds-muted">草稿 v{report.draft_version} · {report.draft_source==='manual'?'人工修订':'AI 生成，待核对'}</p><Input.TextArea aria-label="周报正文" id="weekly-report-body" value={text} disabled={busy||!allowed('weekly_report.edit')} onChange={e=>{const next=e.target.value;setText(next);setDirty(true);try{sessionStorage.setItem(draftKey(report.id),JSON.stringify({text:next,version:report.draft_version}));}catch{setError('本地暂存失败，请及时保存或复制正文');}}} spellCheck={false}/>
      <Button type="primary" onClick={save} disabled={!dirty||!text.trim()||busy||conflict||!allowed('weekly_report.edit')}>保存修改</Button>
      {conflict&&<Button onClick={reloadServer} disabled={busy}>核对服务器最新版本</Button>}</div>:<SbStatePanel state="empty" title="生成后可编辑、保存和复制" description="点击左侧生成周报，完成后在这里核对和保存。"/>}
      <footer className="ds-weekly-output-foot"><span>{dirty?'有未保存修改':report?.result_status==='ready'?'已保存到服务器':'历史版本保存在当前账号下'}</span><span>请核对事实后使用</span></footer>
    </section></div>
  </section>;
}
export default function WeeklyReport(props){
  if(globalThis.SALES_MODE==='preview')return <WeeklyReportDemo {...props}/>;
  const session=globalThis.SalesRuntime.app.globalData.session||{};
  return <LiveReport {...props} session={session} key={`${session.workspaceId}:${session.userId}:${session.role}:${session.permissionVersion}`}/>;
}
