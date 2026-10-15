import {test,beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {collectFollowups,readScopeFor,loadDirectory,normalizeVisit,groupBy} from '../department-ui/pc/data.js';
let actor, calls, respond;
const range={start:'2026-10-01',end:'2026-10-15',today:'2026-10-15'};
const record={id:'v1',recorder_id:'u1',customer_id:'c1',visit_date:'2026-10-10',follow_up_record:'合成跟进'};
beforeEach(()=>{
 actor={workspaceId:'w1',userId:'u1',userName:'合成用户',permissions:{'visit.read':true},permissionGrants:[{permission_code:'visit.read',effect:'allow',scope_code:'self'}]};calls=[];
 respond=()=>({items:[record],summary:{total:1},has_more:false});
 const api={listVisitRange:async p=>{calls.push(p);return respond(p);},getDirectoryMembers:()=>{throw Error('unexpected directory read');}};
 const access={can:(s,k)=>s?.permissions?.[k]===true,identity:s=>[s?.workspaceId,s?.userId,s?.permissionVersion].join(':')};
 globalThis.SalesRuntime={app:{globalData:{session:actor},can:k=>actor.permissions[k]===true},require:n=>n==='utils/access'?access:api};
});
test('range preserves precise recorder/team/date/search filters with pagination',async()=>{
 respond=p=>p.offset?{items:[{...record,id:'v2'}],summary:{total:2},has_more:false}:{items:[record],summary:{total:2},has_more:true,next_offset:1};
 const result=await collectFollowups({range,scope:{kind:'member',memberId:'u2'},query:'客户',directory:{people:new Map()}});
 assert.equal(result.items.length,2);assert.equal(calls[1].offset,1);assert.deepEqual(calls[0].recorder_ids,['u2']);assert.equal(calls[0].q,'客户');assert.equal(calls[0].date_from,range.start);
 await collectFollowups({range,scope:{kind:'team',teamId:'t1'}});assert.equal(calls.at(-1).team_id,'t1');
});
test('mine scope and denied permissions never broaden based on role',async()=>{
 actor.role='administrator';actor.permissions['visit.read']=false;
 assert.deepEqual(readScopeFor(),{allowed:false,scopes:[]});
 await assert.rejects(collectFollowups({range,scope:{kind:'self'}}),/没有/);assert.equal(calls.length,0);
 actor.permissions['visit.read']=true;await collectFollowups({range,scope:{kind:'self'}});assert.equal(calls[0].scope,'mine');
});
test('response loss, changed totals and duplicate pages cannot become complete statistics',async()=>{
 respond=()=>({items:[],summary:{total:1},has_more:false});
 await assert.rejects(collectFollowups({range,scope:{kind:'self'}}),/完整/);
 respond=p=>p.offset?{items:[record],summary:{total:2},has_more:false}:{items:[record],summary:{total:2},has_more:true,next_offset:1};
 await assert.rejects(collectFollowups({range,scope:{kind:'self'}}),/分页已变化/);
 respond=()=>({items:[record],summary:{total:2},has_more:true,next_offset:0});
 await assert.rejects(collectFollowups({range,scope:{kind:'self'}}),/分页异常/);
});
test('changed identity and aborted request do not return old account records',async()=>{
 respond=()=>{actor.userId='different';return {items:[record],summary:{total:1},has_more:false};};
 await assert.rejects(collectFollowups({range,scope:{kind:'self'}}),/身份或权限/);
 const c=new AbortController();c.abort();await assert.rejects(collectFollowups({range,scope:{kind:'self'},signal:c.signal}),/取消/);
});
test('directory is a selector only; no directory grant uses explicit self without broad request',async()=>{
 const directory=await loadDirectory();assert.equal(directory.members.length,1);assert.equal(directory.members[0].id,'u1');assert.equal(directory.complete,false);assert.equal(calls.length,0);
});
test('multiple linked opportunities count one follow-up and unknown dates stay unknown',()=>{
 const row=normalizeVisit({...record,visit_date:null,created_at:'2026-10-15T12:00:00Z',opportunity_ids:['o1','o2'],can_open_customer:false});
 assert.equal(row.date,'');assert.equal(row.canOpenCustomer,false);assert.equal(groupBy([row],'opportunity').length,2);
});
test('linked opportunity keeps its own customer when grouping across customers',()=>{
 const row=normalizeVisit({...record,linked_opportunities:[{id:'o1',name:'A',customer_id:'c1',customer_name:'客户一',can_open:true},{id:'o2',name:'B',customer_id:'c2',customer_name:'客户二',can_open:false}]});
 const groups=groupBy([row],'opportunity');assert.equal(groups.find(g=>g.key==='o2').customerId,'c2');assert.equal(groups.find(g=>g.key==='o2').customerName,'客户二');assert.equal(row.opportunityAccess.o2,false);
});
test('detail lacking link flags retains explicit range denials and cross-customer mapping',async()=>{
 const {loadFollowupDetail}=await import('../department-ui/pc/visit-records.js');
 const row=normalizeVisit({...record,linked_opportunities:[{id:'o1',name:'A',customer_id:'c2',customer_name:'客户二',can_open:false}]});
 const previous=SalesRuntime.require;SalesRuntime.require=name=>name==='utils/apiClient'?{getVisit:async()=>({...record,linked_opportunities:[{id:'o1',name:'A'}],is_summary:false})}:previous(name);
 const detail=await loadFollowupDetail(row);assert.equal(detail.opportunityAccess.o1,false);assert.equal(detail.opportunityCustomers.o1.id,'c2');
});
test('customer count, facets and filters include every linked customer without granting links',async()=>{
 const {summarize}=await import('../department-ui/pc/data.js');const {visitFacets,filterRecordRows}=await import('../department-ui/pc/visit-records.js');
 const row=normalizeVisit({...record,can_open_customer:true,linked_opportunities:[{id:'o1',customer_id:'c1',customer_name:'客户一',can_open:true},{id:'o2',customer_id:'c2',customer_name:'客户二',can_open:true}]});
 assert.equal(summarize([row]).customers,2);assert.equal(summarize([row]).visits,1);assert.equal(visitFacets([row]).customers.length,2);
 assert.equal(filterRecordRows([row],{customer:'c2'}).length,1);assert.equal(groupBy([row],'customer').find(g=>g.key==='c2').title,'客户二');assert.equal(row.customerAccess.c2,undefined);
});
