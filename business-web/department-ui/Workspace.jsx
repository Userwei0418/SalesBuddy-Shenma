import React, {useMemo, useState} from 'react';
import {Button} from 'antd';
import Home from './Home.jsx';
import {NavIcon} from './pc/icons.jsx';
import {FollowupDrawer, FollowupStream} from './pc/followups.jsx';
import {Kpi, LoadState, PageHead, Panel, PeriodSwitch, ScopePicker, useAsync, useSessionState} from './pc/ui.jsx';
import {can, collectFollowups, defaultScope, identity, loadDirectory, open, periodRange, scopeKey, scopeLabel, session, summarize, todayKey} from './pc/data.js';
import {workspacePath} from './pc/workspace-selection.js';
import './pc/workspace.css';
import './pc/workspace-layout.css';
import './pc/overview-filters.css';
import './shenma-workspace.css';

// PC 2.0 layout adapted to Shenma's existing lifecycle and permissions.
// Period/scope controls apply only to follow-ups; existing task metrics keep their original scope.
function Followups({page}) {
  const [period,setPeriod]=useSessionState('workspace-period','day');
  const [savedScope,setScope]=useSessionState('workspace-scope',null);
  const [selected,setSelected]=useState(null);
  const today=todayKey(), range=useMemo(()=>periodRange(period),[period,today]);
  const directory=useAsync(()=>loadDirectory(),[identity()]);
  const scope=savedScope || defaultScope(session(),directory.value);
  const followups=useAsync(signal=> {
    if(directory.error) throw Error(directory.error);
    return directory.value ? collectFollowups({range,scope,directory:directory.value,signal}) : null;
  },[directory.value,directory.error,range.start,range.end,scopeKey(scope)]);
  const rows=followups.value?.items || [], summary=summarize(rows);
  const loading=directory.loading || followups.loading || (!followups.value && !followups.error && !directory.error);
  const error=directory.error || followups.error, partial=followups.value?.coverage.partial;
  const scopeText=scopeLabel(scope,directory.value), selectionKey=[identity(),range.start,range.end,scopeKey(scope)].join(':');
  const detail=(view,linked=false)=>open.route(workspacePath('visit-records',range,scope,{view,...(linked?{linked:'1'}:{})}));
  return <Panel title="跟进概览" note={`${range.title} · ${scopeText}`} extra={<Button aria-label="刷新跟进概览" onClick={()=>{setSelected(null);directory.reload();}}>刷新</Button>}>
    <div className="sm-workspace-filters"><PeriodSwitch value={period} onChange={setPeriod}/>{directory.value && <ScopePicker scope={scope} directory={directory.value} onChange={setScope}/>}</div>
    <div className="pc-kpis">
      <Kpi label="跟进记录" value={loading || error || partial ? null : summary.visits} unit="条" loading={loading} onClick={()=>detail('time')} tone="primary"/>
      <Kpi label="活跃客户" value={loading || error || partial ? null : summary.customers} unit="家" loading={loading} onClick={()=>detail('customer',true)}/>
      <Kpi label="覆盖商机" value={loading || error || partial ? null : summary.opportunities} unit="个" loading={loading} onClick={()=>detail('opportunity',true)}/>
    </div>
    {partial && <p role="alert">记录未读取完整，请缩小时间或人员范围。</p>}
    <LoadState loading={loading} error={error} onRetry={()=>directory.reload()} rows={3}>
      <FollowupStream visits={rows} limit={5} onSelect={visit=>setSelected({visit,key:selectionKey})}/>
      <Button type="link" onClick={()=>detail('time')}>查看全部跟进记录 →</Button>
    </LoadState>
    <FollowupDrawer visit={!loading && !error && selected?.key===selectionKey ? selected.visit : null} onClose={()=>setSelected(null)}/>
  </Panel>;
}

export default function Workspace(props) {
  const {data:d,invoke}=props;
  const quick=[
    ['visit','记录拜访','pages/visit-entry/index','visit.create'],
    ['customer','客户建档','pages/customer-create/index',['customer.create','customer.create_request']],
    ['opportunity','商机列表','pages/workbench/index','opportunity.read'],
    ['task','创建任务','pages/management-task-create/index','task.create'],
    ['report','周报','pages/weekly-report/index','weekly_report.read'],
  ].filter(row=>Array.isArray(row[3])?row[3].some(can):can(row[3]));
  return <div className="pc-page sm-workspace">
    <PageHead title="工作台" subtitle={`${d.greeting || '你好'}${d.userName?`，${d.userName}`:''}`}/>
    {quick.length>0 && <nav className="sm-workspace-quick" aria-label="快捷操作">{quick.map(([icon,label,path])=><Button key={path} icon={<NavIcon name={icon}/>} onClick={()=>open.route(path)}>{label}</Button>)}</nav>}
    <div className="pc-kpis" aria-label="任务概览">{(d.webOverviewMetrics || []).map(metric=><Kpi key={metric.key} label={metric.label} value={metric.value} onClick={()=>invoke('openOverviewTasks',{dataset:{key:metric.key}})}/>)}</div>
    {can('visit.read') && <Followups page={props.page}/>}
    <Home {...props} activityOnly/>
  </div>;
}
