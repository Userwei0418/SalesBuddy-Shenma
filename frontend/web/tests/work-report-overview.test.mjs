import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import * as overviewModule from '../department-ui/work-report/overview.js';
import * as presentationModule from '../department-ui/work-report/presentation.js';
import * as stateModule from '../department-ui/work-report/state.js';
import * as routeModule from '../department-ui/work-report/route.js';
import * as recoveryModule from '../department-ui/work-report/run-recovery.js';
import * as reviewScopeModule from '../department-ui/review/scope-selection.js';
import * as reviewModel from '../department-ui/review/overview-model.js';
import {overviewOptions,overviewScopes,overviewRequest,matchingOverview,boundSnapshotSelection} from '../department-ui/work-report/overview.js';
import {overviewView,meetingView} from '../department-ui/work-report/meeting-presentation.js';
import {createWorkReportApi} from '../department-ui/work-report/api.js';
import {reportSupplements} from '../department-ui/work-report/composition.js';
const id=number=>`00000000-0000-0000-0000-${String(number).padStart(12,'0')}`;
const options=()=>({contract_version:'work_report.overview_options.v1',period_kinds:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','team','member'],
  members:[{id:id(11),name:'成员甲'}],teams:[{id:id(10),name:'示例团队'}],generation:{available_scope_kinds:['self'],managed_teams:[],profiles:['general','sales_week_six']}});
const request=(patch={})=>overviewRequest({period:'week',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general',...patch},options(),id(100));
function response(req=request(),canGenerate=false) {
  const original=JSON.parse(fs.readFileSync(new URL('./fixtures/work-report-meeting/ready-snapshot.response.json',import.meta.url),'utf8'));
  const facts=original.meeting_facts;facts.contract_version='work_report.overview_facts.v1';facts.scope={...facts.scope,...req.selection.scope};
  return {contract_version:'work_report.overview.v1',request_id:req.requestId,selection:req.selection,selection_hash:'a'.repeat(64),overview_hash:'b'.repeat(64),period:facts.period,scope:facts.scope,data_as_of:facts.data_as_of,facts,
    capabilities:{can_view:true,can_generate:canGenerate,generation_unavailable_reason:canGenerate?null:'SCOPE_NOT_GENERATABLE'},
    generation_binding:canGenerate?{token:'SYNTHETIC-OPAQUE-BINDING',expires_at:'2099-01-01T00:00:00+08:00',snapshot_selection:{facts_contract:null,period:req.selection.period,scope:{kind:'team',team_id:id(10)},sources:['visit','opportunity'],supplements:[]}}:null};
}

test('overview scope is the read directory, not the narrower generation directory, for every period',()=>{
  const value=overviewOptions(options());assert.equal(overviewScopes(value).length,3);
  for(const period of ['day','week','biweek','month','quarter'])for(const scopeKey of ['self',`team:${id(10)}`,`member:${id(11)}`]) {
    const row=request({period,scopeKey});assert.equal(row.query.period_kind,period==='biweek'?'two_weeks':period);
    assert.equal(row.query.generation_profile,'general');
    assert.equal(Object.hasOwn(row.query,'team_id'),scopeKey.startsWith('team:'));
    assert.equal(Object.hasOwn(row.query,'member_id'),scopeKey.startsWith('member:'));
  }
  assert.throws(()=>request({scopeKey:`team:${id(999)}`}),/可查看/);
  assert.throws(()=>request({period:'day',profile:'sales_week_six'}),/自然周/);
  assert.throws(()=>request({anchor:'2026-02-30'}),/有效日期/);
});

test('readonly overview preserves authoritative totals without enabling generation or changing the meeting contract',()=>{
  const req=request(),value=response(req);assert.equal(matchingOverview(value,req),value);
  const shown=overviewView(value.facts);assert.equal(shown.amounts[2].change,'-200000.00');assert.equal(shown.scopeLabel,'示例团队');
  assert.throws(()=>boundSnapshotSelection(value,[],{config:{}}),/管理团队/);
  assert.throws(()=>meetingView(value.facts),/事实响应不完整/);
  const missing=response(req);missing.facts.amounts.acv.opening=null;missing.facts.amounts.acv.coverage.opening='unavailable';
  assert.equal(overviewView(missing.facts).amounts[0].opening,null);
});

test('late response, wrong profile, altered resolved scope and inconsistent material time never match a new view',()=>{
  const req=request();
  for(const change of [value=>{value.request_id=id(101);},value=>{value.selection.scope.team_id=id(99);},value=>{value.selection.generation_profile='sales_week_six';},
    value=>{value.scope={...value.scope,team_id:id(99)};},value=>{value.data_as_of='2026-10-11T00:00:00+08:00';},value=>{value.facts={...value.facts,scope:{...value.scope,label:'另一范围'}};}]) {
    const value=structuredClone(response(req));change(value);assert.throws(()=>matchingOverview(value,req),/不一致/);
  }
});

test('bound generation posts the exact server selection with only explicit supplements and opaque binding added',()=>{
  const req=request(),value=response(req,true);matchingOverview(value,req);
  const before=JSON.stringify(value),extra=reportSupplements({supplement:'预计下周回款，请核实。',supplementId:id(30)});
  const body=boundSnapshotSelection(value,extra,{config:{}});
  assert.deepEqual(body,{...value.generation_binding.snapshot_selection,supplements:extra,overview_binding:'SYNTHETIC-OPAQUE-BINDING'});
  assert.equal(body.period,value.generation_binding.snapshot_selection.period);assert.equal(body.scope,value.generation_binding.snapshot_selection.scope);
  assert.equal(body.sources,value.generation_binding.snapshot_selection.sources);assert.equal(JSON.stringify(value),before);
  assert.equal(body.supplements[0].declared_kind,'context');assert.equal(body.facts,undefined);assert.equal(body.data_as_of,undefined);
  assert.throws(()=>boundSnapshotSelection(value,[],{config:{}},Date.parse('2100-01-01')),{code:'WORK_REPORT_OVERVIEW_STALE'});
  assert.throws(()=>boundSnapshotSelection(value,[],{config:{facts_contract:'work_report.sales_week_facts.v1'}}),/类型不一致/);
});

test('task activity must match the whole overview and never changes its frozen generation selection',()=>{
  const req=request(),value=response(req,true),before=JSON.stringify(boundSnapshotSelection(value,[],{config:{}}));
  const fixture=JSON.parse(fs.readFileSync(new URL('./fixtures/work-report-task-activity.response.json',import.meta.url),'utf8')).available.task_activity;
  value.task_activity={...fixture,period:structuredClone(value.period),scope:structuredClone(value.scope),data_as_of:value.data_as_of};
  assert.equal(matchingOverview(value,req),value);
  assert.equal(JSON.stringify(boundSnapshotSelection(value,[],{config:{}})),before);
  for(const key of ['period','scope']) {
    const invalid=structuredClone(value);invalid.task_activity[key].label='过期的范围或周期';
    assert.throws(()=>matchingOverview(invalid,req),/不一致/);
  }
});

test('six-section profile and bound scope cannot silently become general or another department',()=>{
  const req=request({profile:'sales_week_six'}),value=response(req,true);
  assert.throws(()=>matchingOverview(value,req),/不一致/);
  value.generation_binding.snapshot_selection.facts_contract='work_report.sales_week_facts.v1';assert.equal(matchingOverview(value,req),value);
  const supplement=reportSupplements({supplement:'下周预计到账',supplementId:id(31),supplementSection:'next_week_collection',facts_contract:'work_report.sales_week_facts.v1'});
  const body=boundSnapshotSelection(value,supplement,{config:{facts_contract:'work_report.sales_week_facts.v1'}});assert.equal(body.supplements[0].declared_kind,'plan');
  value.generation_binding.snapshot_selection.scope.team_id=id(90);assert.throws(()=>matchingOverview(value,req),/不一致/);
});

test('overview automatically uses GET only, requires no mutation client, and rejects a late identity response',async()=>{
  const calls=[];let owner='first',finish;
  const service=createWorkReportApi({getIdentity:()=>owner,supportsMutation:()=>false,getClient:()=>({request:async row=>{calls.push(row);return {};}})});
  await service.overviewOptions();await service.overview(request().query);
  assert.deepEqual(calls.map(row=>row.method),['GET','GET']);assert.equal(calls[0].path,'/work-reports/overview/options');assert.match(calls[1].path,/\/overview\?request_id=/);
  assert.ok(calls.every(row=>row.fresh && row.data===undefined));
  const delayed=createWorkReportApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>{finish=resolve;})})});
  const pending=delayed.overview(request().query);owner='second';finish(response());await assert.rejects(pending,{code:'REPORT_IDENTITY_CHANGED'});
});

// Finite hooks exercise the real component and shared async hook without a
// browser, network, business storage or a second React renderer dependency.
function componentHarness(file,dependencies={}) {
  const cells=[];let cursor=0,dirty=false,effects=[],component,props,tree;
  const changed=(old,next)=>!old || !next || old.length!==next.length || old.some((value,index)=>!Object.is(value,next[index]));
  const hooks={...React,
    useState(initial){const index=cursor++;cells[index] ||= {value:typeof initial==='function'?initial():initial};return [cells[index].value,value=>{const next=typeof value==='function'?value(cells[index].value):value;if(!Object.is(next,cells[index].value)){cells[index].value=next;dirty=true;}}];},
    useRef(initial){const index=cursor++;return cells[index] ||= {current:initial};},
    useMemo(make,deps){const index=cursor++;if(changed(cells[index]?.deps,deps))cells[index]={deps,value:make()};return cells[index].value;},
    useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const index=cursor++,old=cells[index];if(changed(old?.deps,deps)){cells[index]={deps,cleanup:old?.cleanup};effects.push(()=>{cells[index].cleanup?.();cells[index].cleanup=fn();});}},
  };
  const load=(path,extra={})=>{const module={exports:{}};vm.runInNewContext(transformSync(fs.readFileSync(new URL(`../department-ui/${path}`,import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,
    {module,exports:module.exports,AbortController,URLSearchParams,console,require:name=>name==='react'?hooks:name==='antd'?{Alert:'alert',Button:'button',Input:'input',Select:'select',Spin:'spin'}:
      name.endsWith('data.js')?{identity:()=> 'owner',can:()=>true,session:()=>({userId:id(11)}),runtime:()=>({replacePageQuery(){}})}:
      Object.hasOwn(extra,name)?extra[name]:Object.hasOwn(dependencies,name)?dependencies[name]:{default:name,__esModule:true}});return module.exports;};
  const async=load('pc/ui.jsx');
  const exports=load(file,{'../pc/ui.jsx':async,'./overview.js':overviewModule,'./presentation.js':presentationModule,'./state.js':stateModule,'./route.js':routeModule,
    './run-recovery.js':recoveryModule,'../review/overview-model.js':reviewModel,'../review/scope-selection.js':reviewScopeModule,'../pc/period.js':{todayKey:()=> '2026-10-10'}});
  const render=()=>{cursor=0;dirty=false;tree=component(props);const pending=effects;effects=[];pending.forEach(effect=>effect());return tree;};
  return {exports,start(fn,value){component=fn;props=value;return render();},render,get tree(){return tree;},
    async flush(){for(let i=0;i<30;i++){await new Promise(resolve=>setImmediate(resolve));if(!dirty)return tree;render();}throw Error('hook update did not settle');}};
}
function nodes(tree,predicate) {
  if(!React.isValidElement(tree))return [];
  return [...(predicate(tree)?[tree]:[]),...React.Children.toArray(tree.props.children).flatMap(child=>nodes(child,predicate))];
}

test('overview component reads on entry and filter change, hides old facts immediately, and ignores an out-of-order result',async()=>{
  const pending=[],ready=[];
  const h=componentHarness('work-report/ReportOverview.jsx');
  h.start(h.exports.OverviewState,{owner:'owner',options:options(),initialFilters:{period:'week',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general'},
    service:{overview:query=>new Promise(resolve=>pending.push({query,resolve}))},onReady:value=>ready.push(value)});
  await h.flush();assert.equal(pending.length,1);
  const target=nodes(h.tree,node=>node.props['aria-label']==='查看范围')[0];target.props.onChange('member');h.render();await h.flush();nodes(h.tree,node=>node.props['aria-label']==='选择成员')[0].props.onChange([id(11)]);
  assert.equal(ready.at(-1),null);h.render();await h.flush();assert.equal(pending.length,2);
  assert.equal(nodes(h.tree,node=>node.type==='./MeetingFactsBoard.jsx').length,0);
  const req=request({scopeKey:`member:${id(11)}`});req.requestId=pending[1].query.request_id;
  const latest=response(req);pending[1].resolve(latest);await h.flush();assert.equal(ready.at(-1),latest);
  const review=nodes(h.tree,node=>node.type==='../review/ReviewOverview.jsx')[0];
  assert.deepEqual(review.props.selection.member_ids,[id(11)]);assert.equal(Object.hasOwn(review.props.selection,'member_id'),false);assert.equal(review.props.selection.period_type,'week');
  const previous=request();previous.requestId=pending[0].query.request_id;
  pending[0].resolve(response(previous));await h.flush();assert.equal(ready.at(-1),latest);
  const board=nodes(h.tree,node=>node.type==='./MeetingFactsBoard.jsx')[0];assert.equal(board.props.facts,latest.facts);
  assert.equal(review.props.actions.props.disabled,true);
});

test('overview-to-compose preserves exact material and selection when no generation is pending',async()=>{
  const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const page={options:{period:'week',report_tab:'meeting'}};
  const root=h.exports.default({page,service:{options:async()=>({can_create:true,generation_available:true})}});
  h.start(root.type,root.props);await h.flush();
  assert.deepEqual(Array.from(h.tree.props.disabledTabs),['compose']);
  let overview=nodes(h.tree,node=>node.type==='./ReportOverview.jsx')[0];
  const selected=response(request(),true);overview.props.onReady(selected);h.render();
  h.tree.props.onTabChange('compose');h.render();await h.flush();
  const compose=nodes(h.tree,node=>node.type==='./ReportCompose.jsx')[0];
  assert.equal(compose.props.initialOverview,selected);assert.equal(compose.props.initialAnchor,'2026-10-10');
  assert.equal(compose.props.initialPeriod,'week');assert.equal(compose.props.initialScope.scope,selected.generation_binding.snapshot_selection.scope);
  assert.equal(compose.props.initialRunId,undefined);
});

test('overview never forgets an unresolved generation when the user clicks compose again',async()=>{
  const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const root=h.exports.default({page:{options:{period:'week',report_tab:'meeting',run_id:id(999)}},service:{options:async()=>({can_create:true,generation_available:true})}});
  h.start(root.type,root.props);await h.flush();
  assert.deepEqual(Array.from(h.tree.props.disabledTabs),[]);
  const overview=nodes(h.tree,node=>node.type==='./ReportOverview.jsx')[0];
  overview.props.onReady(response(request(),true));h.render();
  h.tree.props.onTabChange('compose');h.render();await h.flush();
  const compose=nodes(h.tree,node=>node.type==='./ReportCompose.jsx')[0];
  assert.equal(compose.props.initialRunId,id(999));assert.equal(compose.props.initialOverview,undefined);
});

test('target deep link reads no report configuration or generation and returns to overview',async()=>{
  let reads=0;
  const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const root=h.exports.default({page:{options:{report_tab:'targets'}},service:{options:async()=>{reads++;return {};}}});
  h.start(root.type,root.props);await h.flush();
  const target=nodes(h.tree,node=>node.type==='../review/targets/ReviewTargets.jsx')[0];
  assert.ok(target);assert.equal(reads,0);
  assert.equal(nodes(h.tree,node=>node.type==='./ReportCompose.jsx').length,0);
  target.props.onBack();h.render();await h.flush();
  assert.equal(nodes(h.tree,node=>node.type==='./ReportOverview.jsx').length,1);assert.equal(reads,0);
});


test('O/KR entry stays independent of report generation and returns to shared target values',async()=>{
  let reads=0;const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const root=h.exports.default({page:{options:{report_tab:'okr'}},service:{options:async()=>{reads++;return {};}}});
  h.start(root.type,root.props);await h.flush();
  const okr=nodes(h.tree,node=>node.type==='../review/targets/okr/ReviewOkr.jsx')[0];
  assert.ok(okr);assert.equal(reads,0);
  okr.props.onTargets();h.render();await h.flush();
  assert.equal(nodes(h.tree,node=>node.type==='../review/targets/ReviewTargets.jsx').length,1);assert.equal(reads,0);
});

test('scope revision changes require refreshing the overview before another preparation',()=>{
 assert.equal(overviewModule.overviewStale({code:'WORK_REPORT_SCOPE_CHANGED'}),true);
 assert.equal(overviewModule.overviewStale({serverCode:'WORK_REPORT_SCOPE_CHANGED'}),true);
});
