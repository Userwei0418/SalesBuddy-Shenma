import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
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
  await assert.rejects(reports.generate(intent),/timeout/);const key=requests[0].header['Idempotency-Key'];assert.match(key,/^[0-9a-f-]{36}$/);
  reports=loadClient();timeout=false;await reports.generate(intent);assert.equal(requests[1].header['Idempotency-Key'],key);
  await reports.generate(intent);assert.notEqual(requests[2].header['Idempotency-Key'],key,'an acknowledged new intent gets a new receipt');
  await reports.document('synthetic-report');assert.equal(requests[3].header['Idempotency-Key'],undefined);
  timeout=true;
  await assert.rejects(reports.prepare({period:{kind:'week'},overview_binding:'OPAQUE-OVERVIEW-BINDING-NEVER-PERSIST'}),/timeout/);
  assert.doesNotMatch(JSON.stringify([...storage.entries()]),/OPAQUE-OVERVIEW-BINDING-NEVER-PERSIST/,'only a hash receipt may persist, never the material binding token');
  const publish={expected_version:5,revision_id:'22222222-2222-4222-8222-222222222222',confirm:true};
  await assert.rejects(reports.publishFeishu('report',publish),/timeout/);const feishuKey=requests.at(-1).header['Idempotency-Key'];assert.match(feishuKey,/^[0-9a-f-]{36}$/);
  reports=loadClient();timeout=false;await reports.publishFeishu('report',publish);assert.equal(requests.at(-1).header['Idempotency-Key'],feishuKey);assert.deepEqual(JSON.parse(JSON.stringify(requests.at(-1).data)),publish);
  await reports.feishu('report');assert.match(requests.at(-1).url,/\/work-reports\/documents\/report\/feishu$/);assert.equal(requests.at(-1).header['Idempotency-Key'],undefined);
});
