import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {transformSync} from 'esbuild';
import {createWorkReportApi} from '../department-ui/work-report/api.js';
import * as overview from '../department-ui/work-report/overview.js';
import * as presentation from '../department-ui/work-report/presentation.js';
import * as state from '../department-ui/work-report/state.js';
import * as composition from '../department-ui/work-report/composition.js';
import * as defaults from '../department-ui/work-report/default-schemes.js';
import * as sales from '../department-ui/work-report/sales-week.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const saved={id:id(201),name:'合成通用周报',version_no:2,config:{default_period:'week',default_sources:['task'],sections:[],style:{tone:'concise',length:'short'}}};
function response(){
  const facts=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/work-report-meeting/ready-snapshot.response.json'))).meeting_facts;
  facts.contract_version='work_report.overview_facts.v1';facts.scope={...facts.scope,kind:'team',team_id:id(10),member_id:null};
  const period={kind:'week',anchor_date:'2026-10-10',timezone:'Asia/Shanghai'},scope={kind:'team',team_id:id(10)};
  return {selection:{period,scope,generation_profile:'general'},period:facts.period,scope:facts.scope,overview_hash:'b'.repeat(64),facts,
    capabilities:{can_generate:true},generation_binding:{token:'SYNTHETIC-EXPIRED-BINDING',expires_at:'2099-01-01T00:00:00+08:00',snapshot_selection:{period,scope,sources:['visit','opportunity'],supplements:[]}}};
}

// Use the shipped shared client, not a hand-crafted Error. The synthetic wx
// transport is the network boundary; it reproduces the real HTTP error shape.
function transport(){
  const requests=[],storage=new Map();
  const context=vm.createContext({window:{},console,setTimeout,clearTimeout,wx:{
    getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    request:options=>{requests.push({method:options.method,url:options.url});options.success({statusCode:409,data:{detail:'概览已过期或材料已变化，请刷新后重新生成',code:'WORK_REPORT_OVERVIEW_STALE',retryable:false}});},
  }});
  vm.runInContext(fs.readFileSync(path.resolve(root,process.env.SALES_WEB_DIST_DIR || 'dist','bundle.js'),'utf8'),context);
  const cache=new Map();
  function load(name,from=''){
    const key=path.posix.normalize(name.startsWith('.')?path.posix.join(path.posix.dirname(from),name):name).replace(/\.js$/,'');
    if(cache.has(key))return cache.get(key).exports;
    const module={exports:{}};cache.set(key,module);
    vm.runInContext(`(function(require,module,exports){${context.window.SALES_BUNDLE.modules[key]}\n})`,context)(name=>load(name,key),module,module.exports);
    return module.exports;
  }
  const client=load('utils/apiClient'),receipts=load('utils/requestIdentity');
  client.saveAuth({access_token:'SYNTHETIC-ONLY',actor:{workspace_id:'synthetic-workspace',user_id:'synthetic-member'}});
  return {requests,reports:createWorkReportApi({getClient:()=>client,getIdentity:()=> 'synthetic-identity',supportsMutation:receipts.supports})};
}

// Finite hooks run the unchanged Compose and shared async/polling logic. No
// browser, timers for fake successful work, API, database or model is started.
function composeHarness(service,{initial=response(),builtinSchemes=[],defaultScheme=saved.config,generationAvailable=true}={}){
  const cells=[];let cursor=0,dirty=false,effects=[],tree,component,props,back=0;
  const changed=(a,b)=>!a || !b || a.length!==b.length || a.some((x,i)=>!Object.is(x,b[i]));
  const hooks={...React,
    useState(initial){const i=cursor++;cells[i] ||= {value:typeof initial==='function'?initial():initial};return [cells[i].value,v=>{const next=typeof v==='function'?v(cells[i].value):v;if(!Object.is(next,cells[i].value)){cells[i].value=next;dirty=true;}}];},
    useRef(initial){const i=cursor++;return cells[i] ||= {current:initial};},
    useMemo(make,deps){const i=cursor++;if(changed(cells[i]?.deps,deps))cells[i]={deps,value:make()};return cells[i].value;},
    useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const i=cursor++,old=cells[i];if(changed(old?.deps,deps)){cells[i]={deps,cleanup:old?.cleanup};effects.push(()=>{cells[i].cleanup?.();cells[i].cleanup=fn();});}},
    useImperativeHandle(){},forwardRef:fn=>fn,
  };
  const extras={'./MaterialReview.jsx':{default:'material-review',__esModule:true,ReportFilters:'report-filters',ReportMetrics:'report-metrics',ReportSupplement:'report-supplement'},
    './ReportFrame.jsx':{ReportBlank:'report-blank',ReportStatus:'report-status'},'./DocumentEditor.jsx':{RunProgress:'run-progress'},
    './overview.js':overview,'./presentation.js':presentation,'./state.js':state,'./composition.js':composition,'./default-schemes.js':defaults,'./sales-week.js':sales,
    './useLeaveGuard.js':{useLeaveGuard:()=>fn=>fn()},'./api.js':{workReportApi:{}},'../pc/period.js':{todayKey:()=> '2026-10-10'}};
  const load=file=>{const module={exports:{}};vm.runInNewContext(transformSync(fs.readFileSync(path.join(root,'department-ui',file),'utf8'),{loader:'jsx',format:'cjs'}).code,
    {module,exports:module.exports,crypto:webcrypto,AbortController,URLSearchParams,setTimeout,clearTimeout,console,require:name=>name==='react'?hooks:
      name==='antd'?{Alert:'alert',Button:'button',Select:'select',Spin:'spin',Checkbox:{Group:'checkbox'}}:
      name.endsWith('data.js')?{identity:()=> 'owner'}:extras[name] || {default:name,__esModule:true}});return module.exports;};
  extras['../pc/ui.jsx']=load('pc/ui.jsx');extras['./usePollingResource.js']=load('work-report/usePollingResource.js');
  const outer=load('work-report/ReportCompose.jsx').default({owner:'owner',service,initialOverview:initial,initialPeriod:'week',initialAnchor:'2026-10-10',initialScope:{scope:initial.selection.scope},
    options:{can_create:true,generation_available:generationAvailable,available_scope_kinds:['self','team'],members:[],teams:[{id:id(10),name:'团队'}],periods:['week'],default_scheme:defaultScheme,builtin_schemes:builtinSchemes,
      sources:['visit','opportunity','task'].map(source=>({id:source,label:source,available:true}))},onOverview:()=>{back++;}});
  component=outer.type;props=outer.props;
  const render=()=>{cursor=0;dirty=false;tree=component(props);const tasks=effects;effects=[];tasks.forEach(fn=>fn());};
  render();
  return {get tree(){return tree;},get back(){return back;},async flush(){for(let i=0;i<30;i++){await new Promise(resolve=>setImmediate(resolve));if(!dirty)return;render();}throw Error('hook did not settle');},dispose(){cells.forEach(c=>c?.cleanup?.());}};
}
function nodes(tree,predicate){return !React.isValidElement(tree)?[]:[...(predicate(tree)?[tree]:[]),...React.Children.toArray(tree.props.children).flatMap(child=>nodes(child,predicate))];}

for(const generationAvailable of [true,false])test(`ready material promotes the toolbar action without bypassing generation availability (${generationAvailable})`,async()=>{
  const calls=[];let selection,rejectGeneration;
  const service={schemes:async()=>({items:[saved],has_more:false}),prepare:async value=>{selection=value;calls.push('prepare');return{id:id(501)};},
    snapshot:async()=>({id:id(501),status:'ready',snapshot_hash:'c'.repeat(64),selection,period:{label:'本周'},scope:{label:'团队'},coverage:[],metrics:[],data_as_of:'2026-10-10T08:00:00+08:00'}),
    generate:payload=>{calls.push(payload);return new Promise((resolve,reject)=>{rejectGeneration=reject;});}};
  const h=composeHarness(service,{generationAvailable});
  const button=()=>nodes(h.tree,node=>node.props.label==='当前概览对应报告')[0].props.actions;
  try{
    await h.flush();assert.equal(button().props.children,'准备材料');button().props.onClick();await h.flush();
    assert.equal(button().props.children,'生成报告');assert.equal(button().props.disabled,!generationAvailable);
    assert.deepEqual(calls,['prepare'],'preparation does not automatically generate');
    button().props.onClick();await h.flush();
    if(!generationAvailable){assert.deepEqual(calls,['prepare']);return;}
    assert.equal(button().props.children,'正在生成报告');assert.equal(button().props.disabled,true);
    button().props.onClick();await h.flush();assert.equal(calls.length,2,'no duplicate generation while submitting');
    assert.deepEqual(JSON.parse(JSON.stringify(calls[1])),{snapshot_id:id(501),snapshot_hash:'c'.repeat(64),scheme:{scheme_id:saved.id,version_no:saved.version_no}});
    rejectGeneration(Error('合成服务暂不可用'));await h.flush();
    assert.equal(button().props.children,'生成报告');assert.equal(button().props.disabled,false);
    assert.equal(nodes(h.tree,node=>node.props.title==='合成服务暂不可用').length,1);
    assert.equal(nodes(h.tree,node=>node.type==='material-review').length,1,'failure retains prepared material');
  }finally{h.dispose();}
});

test('actual shared client HTTP_409 preserves a business stale code which invalidates the overview binding',async()=>{
  const {reports}=transport();let failure;
  try{await reports.prepare(overview.boundSnapshotSelection(response(),[],saved));}catch(error){failure=error;}
  assert.equal(failure.code,'HTTP_409');assert.equal(failure.serverCode,'WORK_REPORT_OVERVIEW_STALE');assert.equal(overview.overviewStale(failure),true);
  assert.equal(overview.overviewStale({code:'WORK_REPORT_OVERVIEW_STALE'}),true);
  assert.equal(overview.overviewStale({code:'HTTP_409',serverCode:'IDEMPOTENCY_CONFLICT'}),false);
});

test('actual client through Compose disables repeat preparation and offers refresh after a stale response',async()=>{
  const {reports,requests}=transport();let polled=0;
  const h=composeHarness({schemes:async()=>({items:[saved],has_more:false}),prepare:reports.prepare,snapshot:async()=>{polled++;throw Error('must not poll a rejected preparation');}});
  try{
    await h.flush();const button=()=>nodes(h.tree,node=>node.props.label==='当前概览对应报告')[0].props.actions;
    assert.equal(button().props.disabled,false);button().props.onClick();await h.flush();
    assert.equal(button().props.disabled,true);
    const warning=nodes(h.tree,node=>node.props.title==='概览材料已变化或过期，请刷新后再生成');assert.equal(warning.length,1);
    button().props.onClick();await h.flush();assert.equal(requests.length,1);assert.equal(polled,0);
    warning[0].props.action.props.onClick();assert.equal(h.back,1);
  }finally{h.dispose();}
});

for(const profile of ['general','sales_week_six'])test(`new account prepares ${profile} from overview without creating a personal scheme`,async()=>{
  const initial=response(),calls=[];
  initial.selection.generation_profile=profile;
  initial.generation_binding.snapshot_selection.facts_contract=profile==='sales_week_six'?sales.SALES_WEEK_FACTS:null;
  const builtinSchemes=[null,sales.SALES_WEEK_FACTS].map((facts_contract,index)=>({scheme_id:id(301+index),version_no:1,name:`默认周报${index}`,config_hash:String(index).repeat(64),
    config:{...saved.config,facts_contract}}));
  const h=composeHarness({schemes:async()=>({items:[],has_more:false}),prepare:async body=>{calls.push(body);throw Error('Synthetic boundary: request captured, no API started');}},
    {initial,builtinSchemes,defaultScheme:{...saved.config,facts_contract:sales.SALES_WEEK_FACTS}});
  try{
    await h.flush();const button=nodes(h.tree,node=>node.props.label==='当前概览对应报告')[0].props.actions;
    assert.equal(button.props.disabled,false);
    const selector=nodes(h.tree,node=>node.props['aria-label']==='使用报告方案')[0];
    assert.equal(selector.props.value,id(profile==='general'?301:302));
    assert.equal(calls.length,0);button.props.onClick();await h.flush();assert.equal(calls.length,1);
    assert.equal(calls[0].overview_binding,initial.generation_binding.token);
    assert.equal(calls[0].facts_contract,initial.generation_binding.snapshot_selection.facts_contract);
    assert.deepEqual(calls[0].scope,initial.generation_binding.snapshot_selection.scope);
  }finally{h.dispose();}
});
