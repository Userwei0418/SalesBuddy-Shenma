import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createReviewOverviewApi} from '../department-ui/review/overview-api.js';
import {REVIEW_METRICS,REVIEW_ACTIVITIES} from '../department-ui/review/overview-model.js';
import {createWorkReportApi,validatePage} from '../department-ui/work-report/api.js';

test('report writes require the shared idempotency integration; reads are freshly authorized',async()=>{
  const requests=[];let ready=false;
  const reports=createWorkReportApi({getIdentity:()=> 'workspace:member:permission-version',getClient:()=>({request:async options=>{requests.push(options);return {};}}),supportsMutation:()=>ready});
  await assert.rejects(()=>reports.generate({snapshot_id:'snapshot'}),/尚未配套/);assert.equal(requests.length,0);
  await reports.document('report');assert.equal(requests[0].fresh,true);assert.equal(requests[0].path,'/work-reports/documents/report');
  ready=true;await reports.saveRevision('report',{version_no:2,title:'new title'});assert.equal(requests[1].method,'POST');assert.deepEqual(requests[1].data,{version_no:2,title:'new title'});
});

test('identity changes reject late data and errors from the preceding account',async()=>{
  let owner='account-a',resolve,reject;
  const reports=createWorkReportApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise((a,b)=>{resolve=a;reject=b;})})});
  const read=reports.document('private-report');owner='account-b';resolve({text:'old private report'});await assert.rejects(read,{code:'REPORT_IDENTITY_CHANGED'});
  const denied=reports.document('other-report');owner='account-c';reject(Error('old request error'));await assert.rejects(denied,{code:'REPORT_IDENTITY_CHANGED'});
});

test('partial pages must advance and preserve authoritative total and offset',()=>{
  const page={items:[{id:'report-a'}],total:3,limit:40,offset:0,has_more:true,next_offset:1};assert.equal(validatePage(page),page);
  for(const broken of [
    {...page,items:[]},{...page,has_more:false,next_offset:null},{...page,next_offset:0},{...page,total:null},{...page,offset:1},{...page,total:0,has_more:false,next_offset:null},
  ])assert.throws(()=>validatePage(broken),/分页/);
  const last={items:[],total:0,limit:40,offset:0,has_more:false,next_offset:null};assert.equal(validatePage(last),last);
});

test('generated Web client keeps one report mutation receipt across timeout and reload',async()=>{
  const web=fileURLToPath(new URL('../',import.meta.url));
  const bundle=fs.readFileSync(path.resolve(web,process.env.SALES_WEB_DIST_DIR || 'dist','bundle.js'),'utf8');
  const storage=new Map(),requests=[];let timeout=true;
  const loadClient=()=>{
    const context=vm.createContext({window:{},console,setTimeout,clearTimeout,wx:{
      getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
      request:options=>{requests.push(options);assert.match(options.url,/^\/api\/v1\/work-reports\//);if(timeout)options.fail({errMsg:'synthetic timeout'});else options.success({statusCode:200,data:{id:'synthetic-run'}});},
    }});
    vm.runInContext(bundle,context);const cache=new Map();
    function load(name,from=''){
      const id=path.posix.normalize(name.startsWith('.')?path.posix.join(path.posix.dirname(from),name):name).replace(/\.js$/,'');
      if(cache.has(id))return cache.get(id).exports;
      const module={exports:{}};cache.set(id,module);
      vm.runInContext(`(function(require,module,exports){${context.window.SALES_BUNDLE.modules[id]}\n})`,context)(name=>load(name,id),module,module.exports);return module.exports;
    }
    const client=load('utils/apiClient'),receipts=load('utils/requestIdentity');
    client.saveAuth({access_token:'SYNTHETIC-ONLY',actor:{workspace_id:'synthetic-workspace',user_id:'synthetic-member'}});
    return createWorkReportApi({getClient:()=>client,getIdentity:()=> 'synthetic-identity',supportsMutation:receipts.supports});
  };
  let reports=loadClient();const intent={snapshot_id:'synthetic-snapshot',snapshot_hash:'synthetic-hash'};
  await assert.rejects(reports.generate(intent),{code:'NETWORK_ERROR',timeout:true,outcomeUnknown:true,recoveryAction:'check_status'});const key=requests[0].header['Idempotency-Key'];assert.match(key,/^[0-9a-f-]{36}$/);
  reports=loadClient();timeout=false;await reports.generate(intent);assert.equal(requests[1].header['Idempotency-Key'],key);
  await reports.generate(intent);assert.notEqual(requests[2].header['Idempotency-Key'],key,'an acknowledged new intent gets a new receipt');
  await reports.document('synthetic-report');assert.equal(requests[3].header['Idempotency-Key'],undefined);
  timeout=true;
  await assert.rejects(reports.prepare({period:{kind:'week'},overview_binding:'OPAQUE-OVERVIEW-BINDING-NEVER-PERSIST'}),{code:'NETWORK_ERROR',timeout:true,outcomeUnknown:true,recoveryAction:'check_status'});
  assert.doesNotMatch(JSON.stringify([...storage.entries()]),/OPAQUE-OVERVIEW-BINDING-NEVER-PERSIST/,'only a hash receipt may persist, never the material binding token');
});


test('optional overview aborts only its read task and always unbinds the signal listener',async()=>{
 const controller=new AbortController();let finish,requests=0,aborts=0,added=0,removed=0;
 const signal={get aborted(){return controller.signal.aborted;},addEventListener(...args){added++;controller.signal.addEventListener(...args);},removeEventListener(...args){removed++;controller.signal.removeEventListener(...args);}};
 const reports=createWorkReportApi({getIdentity:()=> 'owner',getClient:()=>({request:options=>{requests++;assert.equal(options.method,'GET');assert.equal(options.fresh,true);assert.equal(options.timeout,30000);options.onRequestTask({abort(){aborts++;}});return new Promise(resolve=>{finish=resolve;});}})});
 const pending=reports.overview({scope_kind:'team'},{signal});controller.abort();finish({old:true});
 await assert.rejects(pending,{code:'REQUEST_CANCELLED'});assert.equal(aborts,1);assert.equal(added,1);assert.equal(removed,1);
 await assert.rejects(reports.overview({scope_kind:'team'},{signal}),{code:'REQUEST_CANCELLED'});assert.equal(requests,1);
 const completed=new AbortController();let completedAborts=0;
 const fast=createWorkReportApi({getIdentity:()=> 'owner',getClient:()=>({request:async options=>{options.onRequestTask({abort(){completedAborts++;}});return {ok:true};}})});
 assert.deepEqual(await fast.overview({}, {signal:completed.signal}),{ok:true});completed.abort();assert.equal(completedAborts,0);
});


test('only the four aggregate report GETs extend the actual shared transport to 30 seconds',async()=>{
 const selection={scope:'self',anchor_date:'2026-10-14',period_type:'day'};
 const envelope={selection:{...selection,team_id:null,member_id:null},scope:{kind:'self',label:'本人',member_ids:['actor']},period:{label:'2026-10-14'},data_as_of:'2026-10-14T10:00:00Z'};
 const requests=[],storage=new Map();
 const context=vm.createContext({window:{},console,setTimeout,clearTimeout,wx:{
  getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
  request:options=>{
   requests.push(options);const url=new URL(options.url,'https://synthetic.invalid');let data={id:'synthetic-run'};
   if(url.pathname.endsWith('/review/overview'))data={...envelope,contract_version:'review.overview.v1',quarter:{quarter:4,time_progress:'0.15'},members:[],
    metrics:REVIEW_METRICS.map(({code})=>({metric_code:code,value:'0.00',known_subtotal:'0.00',coverage:'complete',target:{value:null,status:'no_target'},gap:null,rate:null,day_change:null,baseline:{status:'no_baseline'}})),
    activities:REVIEW_ACTIVITIES.map(({code})=>({code,count:0,known_count:0,coverage:'complete'}))};
   else if(url.pathname.includes('/review/'))data={...envelope,contract_version:'review.page.v1',activity:url.searchParams.get('activity'),filter:url.searchParams.get('filter'),limit:20,offset:0,items:[],known_total:0,total:0,coverage:'complete',has_more:false};
   options.success({statusCode:200,data});
  },
 }});
 const web=fileURLToPath(new URL('../',import.meta.url));
 vm.runInContext(fs.readFileSync(path.resolve(web,process.env.SALES_WEB_DIST_DIR || 'dist','bundle.js'),'utf8'),context);
 const cache=new Map();
 function load(name,from=''){
  const id=path.posix.normalize(name.startsWith('.')?path.posix.join(path.posix.dirname(from),name):name).replace(/\.js$/,'');
  if(cache.has(id))return cache.get(id).exports;
  const module={exports:{}};cache.set(id,module);
  vm.runInContext(`(function(require,module,exports){${context.window.SALES_BUNDLE.modules[id]}\n})`,context)(name=>load(name,id),module,module.exports);return module.exports;
 }
 const client=load('utils/apiClient');client.saveAuth({access_token:'SYNTHETIC-ONLY',actor:{workspace_id:'synthetic-workspace',user_id:'actor'}});
 const dependencies={getClient:()=>client,getIdentity:()=> 'synthetic-identity',supportsMutation:load('utils/requestIdentity').supports};
 const review=createReviewOverviewApi(dependencies),report=createWorkReportApi(dependencies);
 await review.overview(selection);await review.activities(selection,{activity:'formal_visits',limit:20,offset:0});await review.opportunities(selection,{filter:'all',limit:20,offset:0});await report.overview({});
 assert.deepEqual(requests.map(row=>row.timeout),[30000,30000,30000,30000]);
 assert.ok(requests.every(row=>row.method==='GET' && row.timeoutMs===undefined));
 await report.options();await report.overviewOptions();await report.run('run');await report.generate({snapshot_id:'synthetic-snapshot'});await report.prepare({sources:['visit']});
 assert.deepEqual(requests.slice(4).map(row=>row.timeout),[15000,15000,15000,15000,15000]);
 assert.deepEqual(requests.slice(4).map(row=>row.method),['GET','GET','GET','POST','POST']);
});

test('document share preflight uses the authenticated cancellable endpoint without sending a share mutation',async()=>{
 const requests=[];let finish,aborts=0;
 const service=createWorkReportApi({getIdentity:()=> 'owner',supportsMutation:()=>true,getClient:()=>({request:options=>{
  requests.push(options);options.onRequestTask?.({abort(){aborts++;}});return new Promise(resolve=>{finish=resolve;});
 }})});
 const selection={version_no:3,audience:{kind:'team',team_id:'authorized-team'}},controller=new AbortController();
 const pending=service.sharePreview('document',selection,{signal:controller.signal});
 assert.equal(requests[0].path,'/work-reports/documents/document/share-preview');assert.equal(requests[0].method,'POST');
 assert.deepEqual(requests[0].data,selection);assert.equal(requests[0].data.confirm,undefined);
 controller.abort();finish({});await assert.rejects(pending,{code:'REQUEST_CANCELLED'});assert.equal(aborts,1);
 assert.equal(requests.length,1);
});
