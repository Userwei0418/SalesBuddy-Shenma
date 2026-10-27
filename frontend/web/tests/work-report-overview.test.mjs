import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import * as readPolicy from '../department-ui/work-report/read-policy.js';
import * as composeEntryModule from '../department-ui/work-report/compose-entry.js';
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
function componentHarness(file,dependencies={},globals={}) {
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
    {module,exports:module.exports,AbortController,URLSearchParams,console,...globals,require:name=>name==='react'?hooks:name==='antd'?{Alert:'alert',Button:'button',Input:'input',Select:'select',Spin:'spin'}:
      name.endsWith('data.js')?(dependencies[name] || {identity:()=> 'owner',can:()=>true,session:()=>({userId:id(11)}),runtime:()=>({replacePageQuery(){}})}):
      Object.hasOwn(extra,name)?extra[name]:Object.hasOwn(dependencies,name)?dependencies[name]:{default:name,__esModule:true}});return module.exports;};
  const async=load('pc/ui.jsx');
  const exports=load(file,{'../pc/ui.jsx':async,'./overview.js':overviewModule,'./compose-entry.js':composeEntryModule,'./presentation.js':presentationModule,'./state.js':stateModule,'./route.js':routeModule,
    './read-policy.js':readPolicy,'./run-recovery.js':recoveryModule,'../review/overview-model.js':reviewModel,'./overview-model.js':reviewModel,'../review/scope-selection.js':reviewScopeModule,'../pc/period.js':{todayKey:()=> '2026-10-10'}});
  const render=()=>{cursor=0;dirty=false;tree=component(props);const pending=effects;effects=[];pending.forEach(effect=>effect());return tree;};
  return {exports,start(fn,value){component=fn;props=value;return render();},render,update(value){props={...props,...value};return render();},get tree(){return tree;},unmount(){cells.forEach(cell=>cell.cleanup?.());},
    async flush(){for(let i=0;i<30;i++){await new Promise(resolve=>setImmediate(resolve));if(!dirty)return tree;render();}throw Error('hook update did not settle');}};
}
function nodes(tree,predicate) {
  if(!React.isValidElement(tree))return [];
  return [...(predicate(tree)?[tree]:[]),...React.Children.toArray(tree.props.children).flatMap(child=>nodes(child,predicate))];
}

test('legacy overview loads only when expanded, cancels on selection change and ignores late data',async()=>{
  const pending=[],ready=[];
  const h=componentHarness('work-report/ReportOverview.jsx');
  h.start(h.exports.OverviewState,{owner:'owner',options:options(),initialFilters:{period:'week',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general'},
    service:{overview:(query,{signal})=>new Promise(resolve=>pending.push({query,resolve,signal}))},onReady:value=>ready.push(value)});
  await h.flush();assert.equal(pending.length,0);
  const details=()=>nodes(h.tree,node=>node.type==='details' && node.props.className==='rv-previous-facts')[0];
  details().props.onToggle({currentTarget:{open:true}});h.render();await h.flush();assert.equal(pending.length,1);
  details().props.onToggle({currentTarget:{open:true}});h.render();await h.flush();assert.equal(pending.length,1);
  const target=nodes(h.tree,node=>node.props['aria-label']==='查看范围')[0];target.props.onChange('member');h.render();await h.flush();nodes(h.tree,node=>node.props['aria-label']==='选择成员')[0].props.onChange([id(11)]);
  assert.equal(ready.at(-1),null);h.render();await h.flush();assert.equal(pending.length,1);assert.equal(pending[0].signal.aborted,true);assert.equal(details().props.open,false);
  details().props.onToggle({currentTarget:{open:true}});h.render();await h.flush();assert.equal(pending.length,2);
  assert.equal(nodes(h.tree,node=>node.type==='./MeetingFactsBoard.jsx').length,0);
  const req=request({scopeKey:`member:${id(11)}`});req.requestId=pending[1].query.request_id;
  const latest=response(req);pending[1].resolve(latest);await h.flush();assert.equal(ready.at(-1),latest);
  const review=nodes(h.tree,node=>node.type==='../review/ReviewOverview.jsx')[0];
  assert.deepEqual(review.props.selection.member_ids,[id(11)]);assert.equal(Object.hasOwn(review.props.selection,'member_id'),false);assert.equal(review.props.selection.period_type,'week');
  const previous=request();previous.requestId=pending[0].query.request_id;
  pending[0].resolve(response(previous));await h.flush();assert.equal(ready.at(-1),latest);
  const board=nodes(h.tree,node=>node.type==='./MeetingFactsBoard.jsx')[0];assert.equal(board.props.facts,latest.facts);
  assert.equal(nodes(review.props.actions,node=>node.type==='button')[0].props.disabled,undefined);
  assert.match(nodes(review.props.actions,node=>node.props.role==='status')[0].props.children,/只能生成本人/);
});

test('report period filters update the real runtime route and shell navigation without remounting or losing scope',async()=>{
  const runtimeCode=fs.readFileSync(new URL('../runtime.js',import.meta.url),'utf8'),shellCode=fs.readFileSync(new URL('../shell.js',import.meta.url),'utf8');
  for(const path of ['pages/work-report/index','pages/weekly-report/index']){
    const page={route:path,_url:`/${path}?period=day&report_tab=meeting&scope=team&team_id=${id(10)}`,options:{period:'day',report_tab:'meeting',scope:'team',team_id:id(10)}};
    let paints=0,replacements=0;
    const nodes=new Map(),frame={},app={globalData:{session:null},can:()=>true};
    const context=vm.createContext({URL,URLSearchParams,current:page,app,bundle:{pages:{[path]:{config:{}}},config:{tabBar:{list:[]}}},
      frame,icons:[],taskIcon:'',visitIcon:'',tabs:[],profileAlias:'',preview:false,crmData:null,innerWidth:1440,hiddenTabs:false,titleOverride:'',navigationEpoch:1,
      SalesRuntime:{current:page,app},location:{hash:''},history:{replaceState(){replacements++;},pushState(){throw Error('must not add a navigation level');}},
      document:{body:{dataset:{},classList:{toggle(){}}},querySelectorAll:()=>[]},accountDialog:{open:false},
      $:key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key);},clearLoginError(){},availableQuickActions:()=>[],updateConnection(){},paintFrame(){paints++;},
      CustomEvent:function(type,options){return {type,...options};},window:{}});
    context.window.dispatchEvent=event=>{assert.equal(event.type,'sales:navigation');context.navigation(event.detail);};
    vm.runInContext(shellCode.slice(shellCode.indexOf('  const navigationTree ='),shellCode.indexOf('  // 手机宽度仍用'))+
      shellCode.slice(shellCode.indexOf('  function navigation('),shellCode.indexOf("  window.addEventListener('sales:navigation'")),context);
    vm.runInContext(runtimeCode.slice(runtimeCode.indexOf('  function updateHash('),runtimeCode.indexOf('  function route(')),context);
    const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'},
      '../pc/data.js':{identity:()=> 'owner',can:()=>false,session:()=>({userId:id(11)}),runtime:()=>({replacePageQuery:context.replacePageQuery})}});
    const root=h.exports.default({page,service:{options(){throw Error('period change must not read generation options');}}});h.start(root.type,root.props);await h.flush();
    const expected={week:'周报 · 本周',biweek:'双周报 · 上周＋本周',month:'月报 · 本月',quarter:'季度报 · 本季度',day:'日报 · 今日'};
    for(const [period,label] of Object.entries(expected)){
      const filters={period,anchor:'2026-10-10',profile:'general',scopeKey:`team:${id(10)}`};
      nodes.clear();const overview=nodesInOverview(h.tree);overview.props.onSelection(filters);h.render();await h.flush();
      assert.equal(frame.activeKey,`reports.${period}`);assert.equal(frame.title,label);assert.deepEqual(Array.from(frame.crumbs),['工作报告',label]);
      assert.equal(context.current,page);assert.equal(page.options.period,period);assert.equal(page.options.review_anchor,filters.anchor);
      assert.equal(page.options.review_subjects,id(10));assert.equal(page.options.team_id,id(10));assert.equal(page.options.report_tab,'meeting');
      const previous=paints;nodesInOverview(h.tree).props.onSelection({...filters});h.render();await h.flush();assert.equal(paints,previous,'unchanged period must not repeat navigation');
    }
    assert.equal(paints,5);assert.equal(replacements,10);
    assert.equal(context.replacePageQuery({...page},{period:'week'}),false);page._destroyed=true;assert.equal(context.replacePageQuery(page,{period:'week'}),false);
  }
});
function nodesInOverview(tree){return nodes(tree,node=>node.type==='./ReportOverview.jsx')[0];}

test('overview-to-compose preserves exact material and selection when no generation is pending',async()=>{
  const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const page={options:{period:'week',report_tab:'meeting'}};
  const root=h.exports.default({page,service:{options:async()=>({can_create:true,generation_available:true})}});
  h.start(root.type,root.props);await h.flush();
  assert.equal(h.tree.props.disabledTabs,undefined);
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
  assert.equal(h.tree.props.disabledTabs,undefined);
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


test('generation navigation preserves a denied or unread scope and never posts or invents a signed binding',async()=>{
 for(const unread of [true,false]){
  let reads=0;
  const h=componentHarness('work-report/ReportWorkspace.jsx',{'./ReportFrame.jsx':{ReportFrame:'frame'}});
  const root=h.exports.default({page:{options:{period:'week',report_tab:'meeting'}},service:{options:async()=>{reads++;return {can_create:false,generation_available:false};},prepare(){throw Error('navigation must not submit');},generate(){throw Error('navigation must not submit');}}});
  h.start(root.type,root.props);await h.flush();
  const filters={period:'month',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general'};
  nodesInOverview(h.tree).props.onSelection(filters);h.render();
  if(!unread){const denied=response(request(filters));denied.capabilities.generation_unavailable_reason='SOURCE_NOT_GENERATABLE';nodesInOverview(h.tree).props.onReady(denied);h.render();}
  h.tree.props.onTabChange('compose');h.render();await h.flush();
  const compose=nodes(h.tree,node=>node.type==='./ReportCompose.jsx')[0];
  assert.equal(reads,1);assert.equal(compose.props.initialPeriod,'month');assert.equal(compose.props.initialAnchor,filters.anchor);
  assert.equal(compose.props.initialScope.scope.kind,'team');
  assert.equal(compose.props.initialScope.scope.team_id || compose.props.initialScope.scope.team_ids[0],id(10));
  assert.equal(compose.props.initialOverview,undefined);assert.equal(compose.props.options.can_create,false);
  assert.doesNotMatch(compose.props.initialNotice,/本次材料不满足生成权限/);
  if(unread)assert.equal(compose.props.initialNotice,'已保留当前周期和范围，请在设置中核对授权材料后准备。');
 }
 assert.equal(composeEntryModule.overviewGenerateHint({value:{capabilities:{can_generate:false,generation_unavailable_reason:'SOURCE_NOT_GENERATABLE'}}}),'进入设置，重新选择授权材料。');
});


test('optional legacy failure is local and retryable; closing or switching identity discards the pending result',async()=>{
 let owner='owner';const pending=[],ready=[];
 const h=componentHarness('work-report/ReportOverview.jsx',{'../pc/data.js':{identity:()=>owner},'./data.js':{identity:()=>owner}});
 h.start(h.exports.OverviewState,{owner:'owner',options:options(),initialFilters:{period:'week',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general'},
   service:{overview:(query,{signal})=>new Promise((resolve,reject)=>pending.push({query,signal,resolve,reject}))},onReady:value=>ready.push(value)});
 await h.flush();assert.equal(pending.length,0);
 const details=()=>nodes(h.tree,node=>node.type==='details' && node.props.className==='rv-previous-facts')[0];
 details().props.onToggle({currentTarget:{open:true}});h.render();await h.flush();
 pending[0].reject(Error('读取超时，请稍后重试'));await h.flush();
 const alert=nodes(details(),node=>node.type==='alert')[0];assert.equal(alert.props.description,'读取超时，请稍后重试');
 assert.equal(nodes(h.tree,node=>node.type==='alert').length,1,'failure appears only inside the optional section');
 assert.equal(nodes(h.tree,node=>node.type==='../review/ReviewOverview.jsx').length,1);
 alert.props.action.props.onClick();h.render();await h.flush();assert.equal(pending.length,2);
 details().props.onToggle({currentTarget:{open:false}});h.render();await h.flush();assert.equal(pending[1].signal.aborted,true);
 const req=request();req.requestId=pending[1].query.request_id;pending[1].resolve(response(req,true));await h.flush();
 assert.equal(ready.at(-1),null);assert.equal(nodes(h.tree,node=>node.type==='./MeetingFactsBoard.jsx').length,0);
 details().props.onToggle({currentTarget:{open:true}});h.render();await h.flush();assert.equal(pending.length,3);
 owner='other';h.render();await h.flush();assert.equal(pending[2].signal.aborted,true);assert.equal(pending.length,3);
 const late=request();late.requestId=pending[2].query.request_id;pending[2].resolve(response(late,true));await h.flush();assert.equal(h.tree,null);
});


test('review loading and errors retain current selectors and settings navigation while cancelling discarded reads',async()=>{
 let owner='owner';const pending=[],loading=[],selection={scope:'self',anchor_date:'2026-10-10',period_type:'day'};
 const h=componentHarness('review/ReviewOverview.jsx',{'./data.js':{identity:()=>owner},'../pc/data.js':{identity:()=>owner}});
 h.start(h.exports.default,{selection,pendingTitle:'10月10日 · 本人',actions:React.createElement('button',{id:'settings'},'生成报告'),onLoadingChange:value=>loading.push(value),
  service:{overview:(selected,{signal})=>new Promise((resolve,reject)=>pending.push({selected,signal,resolve,reject}))}});
 await h.flush();assert.equal(pending.length,1);assert.equal(loading.at(-1),true);
 const title=()=>nodes(h.tree,node=>node.type==='h2')[0]?.props.children;
 assert.equal(title(),'10月10日 · 本人');assert.equal(nodes(h.tree,node=>node.props.id==='settings').length,1);
 h.update({selection:{...selection,anchor_date:'2026-10-11'},pendingTitle:'10月11日 · 本人'});await h.flush();
 assert.equal(pending[0].signal.aborted,true);assert.equal(pending.length,2);assert.equal(title(),'10月11日 · 本人');
 pending[0].resolve({data_as_of:'old'});await h.flush();assert.equal(title(),'10月11日 · 本人');
 pending[1].reject(Error('读取超时，请稍后重试'));await h.flush();assert.equal(loading.at(-1),false);
 assert.equal(title(),'10月11日 · 本人');assert.equal(nodes(h.tree,node=>node.props.id==='settings').length,1);
 const error=nodes(h.tree,node=>node.type==='alert')[0];assert.equal(error.props.title,'读取超时，请稍后重试');
 error.props.action.props.onClick();h.render();await h.flush();assert.equal(pending.length,3);assert.equal(loading.at(-1),true);
 owner='other';h.render();await h.flush();assert.equal(pending[2].signal.aborted,true);
 pending[2].resolve({data_as_of:'old identity'});await h.flush();assert.equal(nodes(h.tree,node=>node.props.value?.data_as_of==='old identity').length,0);
});

test('overview refresh is disabled during the current read and old loading callbacks cannot unlock a new scope',async()=>{
 const h=componentHarness('work-report/ReportOverview.jsx');
 h.start(h.exports.OverviewState,{owner:'owner',options:options(),initialFilters:{period:'week',anchor:'2026-10-10',scopeKey:`team:${id(10)}`,profile:'general'},service:{overview(){throw Error('not expanded');}}});
 await h.flush();const refresh=()=>nodes(h.tree,node=>node.props.label==='本期概览条件')[0].props.actions,review=()=>nodes(h.tree,node=>node.type==='../review/ReviewOverview.jsx')[0];
 assert.equal(refresh().props.disabled,true);const original=review().props.selection;refresh().props.onClick();h.render();await h.flush();assert.equal(review().props.selection,original);
 review().props.onLoadingChange(false);h.render();await h.flush();assert.equal(refresh().props.disabled,false);
 const stale=review().props.onLoadingChange;refresh().props.onClick();h.render();await h.flush();assert.equal(refresh().props.disabled,true);
 assert.notEqual(review().props.selection,original);stale(false);h.render();await h.flush();assert.equal(refresh().props.disabled,true);
 review().props.onLoadingChange(false);h.render();await h.flush();assert.equal(refresh().props.disabled,false);
 stale(true);h.render();await h.flush();assert.equal(refresh().props.disabled,false,'an obsolete callback cannot relock the completed current read');
});


test('slow report hint starts after 10 seconds and resets on scope, retry, completion and unmount',async()=>{
 let now=0,serial=0;const timers=new Map();
 const clock={setTimeout(fn,delay){const id=++serial;timers.set(id,{fn,at:now+delay});return id;},clearTimeout(id){timers.delete(id);}};
 const h=componentHarness('work-report/ReportReadStatus.jsx',{},clock);
 const tick=async ms=>{now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}await h.flush();};
 const text=()=>nodes(h.tree,node=>node.type==='span')[0]?.props.children;
 const first=()=>{},second=()=>{};
 h.start(h.exports.default,{loading:false,requestKey:first,label:'正在读取本期业务概览'});assert.equal(h.tree,null);assert.equal(timers.size,0);
 h.update({loading:true});await tick(9999);assert.equal(text(),'正在读取本期业务概览');
 await tick(1);assert.equal(text(),'数据较多，正在继续读取，请稍候');
 h.update({requestKey:second});assert.equal(text(),'正在读取本期业务概览');assert.equal(timers.size,1);
 const old=[...timers.values()][0].fn;await tick(5000);
 h.update({loading:false});assert.equal(h.tree,null);assert.equal(timers.size,0);old();await h.flush();assert.equal(h.tree,null);
 h.update({loading:true});await tick(9999);assert.equal(text(),'正在读取本期业务概览');await tick(1);assert.equal(text(),'数据较多，正在继续读取，请稍候');
 h.update({requestKey:()=>{}});assert.equal(text(),'正在读取本期业务概览');const unmounted=[...timers.values()][0].fn;
 h.unmount();assert.equal(timers.size,0);unmounted();await h.flush();assert.equal(text(),'正在读取本期业务概览');
 const table=componentHarness('work-report/ReportReadStatus.jsx',{},clock);table.start(table.exports.default,{loading:true,requestKey:first});assert.equal(table.tree,null);table.unmount();assert.equal(timers.size,0);
});
