require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const tick=()=>new Promise(r=>setImmediate(r));
function wait(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function make(api){
 const file=path.resolve(__dirname,'../miniprogram/pages/visit-confirm/index.js');let page;const timers=new Map();let timer=0;
 const app={globalData:{session:{workspaceId:'w',userId:'u',role:'sales',permissionVersion:1,loginAt:'one'}}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>page=p,getApp:()=>app,require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(file),n)),wx:{setStorageSync(){},showModal(){},showToast(){}},setTimeout:f=>{timers.set(++timer,f);return timer;},clearTimeout:id=>timers.delete(id)});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=v=>Object.assign(page.data,v);page.persist=()=>{};
 Object.assign(page.data,{customerId:'c',customerConfirmed:true,values:{},reviewRunId:'keep'});
 return {page,app,flush(){const pending=[...timers.values()];timers.clear();pending.forEach(f=>f());}};
}
const row=(id,name=id)=>({id,name,customer_id:'c',amount:120000,probability:30,status:'open',version_no:4,quarterly_forecasts:[{year:2026,quarter:4,recognized_amount:100}],fde_members:[{id:'fde'}]});
const pageOf=(items,more=false,next=null)=>({items,has_more:more,next_offset:next});
test('only first 20 requested; more uses server offset and keeps selected form intact',async()=>{
 const calls=[];const h=make({listOpportunities:async x=>{calls.push(x);return x.offset?pageOf([row('o19'),row('o20')]):pageOf(Array.from({length:20},(_,i)=>row('o'+i)),true,20);},listAllOpportunities(){throw Error('unbounded forbidden');}});
 await h.page.loadOpportunities();assert.equal(calls.length,1);assert.equal(calls[0].pageSize,20);assert.equal(calls[0].includeClosed,true);
 h.page.chooseOpportunity({currentTarget:{dataset:{id:'o2'}}});const selected=h.page.data.selectedOpportunity;h.page.data.opportunityDraft={name:'人工修改'};
 await h.page.moreOpportunities();assert.equal(calls[1].offset,20);assert.equal(h.page.data.opportunityItems.length,21);assert.equal(h.page.data.selectedOpportunity,selected);assert.equal(h.page.data.opportunityDraft.name,'人工修改');assert.equal(h.page.data.opportunityOptions[h.page.data.opportunityIndex].id,'o2');
});
test('draft target outside first page is read exactly with full version/FDE/quarter data',async()=>{
 const exact=[];const target=row('outside');const h=make({listOpportunities:async()=>pageOf([row('first')],true,20),getOpportunityDetailOverview:async id=>{exact.push(id);return {id:'c',opportunities:[target]};}});
 h.page.data.opportunityId='outside';h.page.data.opportunityDraft={name:'草稿修改'};await h.page.loadOpportunities();assert.deepEqual(exact,['outside']);assert.equal(h.page.data.opportunityId,'outside');assert.equal(h.page.data.selectedOpportunity.version_no,4);assert.equal(h.page.data.selectedOpportunity.fde_members.length,1);assert.equal(h.page.data.opportunityDraft.name,'草稿修改');assert.equal(h.page.data.opportunityOptions[h.page.data.opportunityIndex].id,'outside');
});
test('AI name not in first page searches remotely and matches before considering a new opportunity',async()=>{
 const calls=[];const target=row('target','客户数据平台一期');const h=make({listOpportunities:async x=>{calls.push(x);return x.query?pageOf([target]):pageOf([row('first')],true,20);}});
 h.page.aiOpportunitySuggestion={name:target.name,amount:'80',hasContent:true,stageIndex:2};await h.page.loadOpportunities();assert.equal(calls.length,2);assert.equal(calls[1].query,target.name);assert.equal(h.page.data.opportunityId,'target');assert.equal(h.page.data.opportunityDraft.amount,'80');assert.equal(h.page.data.opportunityAIRecognized,true);
});
test('AI explicit ID is retained on lookup failure and never silently converted to new or unlinked',async()=>{
 const h=make({listOpportunities:async()=>pageOf([]),getOpportunityDetailOverview:async()=>{throw Error('无权查看');}});h.page.aiOpportunitySuggestion={opportunityId:'private',name:'项目',hasContent:true};await h.page.loadOpportunities();assert.equal(h.page.data.opportunityId,'private');assert.equal(h.page.data.selectedOpportunity,null);assert.match(h.page.data.blockReason,/核对关联商机/);assert.match(h.page.data.opportunityError,/无权查看/);
 h.page.chooseOpportunity({currentTarget:{dataset:{id:''}}});assert.equal(h.page.data.opportunityId,'');assert.equal(h.page.data.opportunityResolveError,'');
});
test('AI explicit target retains all proposed fields after a failed exact lookup and retry',async()=>{
 let attempts=0;const target=row('target','客户数据平台一期');
 const h=make({listOpportunities:async()=>pageOf([row('first')],true,20),getOpportunityDetailOverview:async()=>{if(++attempts===1)throw Error('网络中断');return {id:'c',opportunities:[target]};}});
 const suggestion={opportunityId:'target',name:target.name,amount:'80',hasContent:true,stageIndex:2};h.page.aiOpportunitySuggestion=suggestion;
 await h.page.loadOpportunities();assert.equal(h.page.data.opportunityId,'target');assert.equal(h.page.aiOpportunitySuggestion,suggestion);assert.equal(h.page.data.opportunityDraft,null);
 await h.page.retryOpportunities();assert.equal(attempts,2);assert.equal(h.page.data.selectedOpportunity.id,'target');assert.equal(h.page.data.opportunityDraft.amount,'80');assert.equal(h.page.data.opportunityDraft.stageIndex,2);assert.equal(h.page.data.opportunityAIRecognized,true);assert.equal(h.page.aiOpportunitySuggestion,null);assert.equal(h.page.data.opportunityResolveError,'');
});
test('hiding during AI target lookup restores the pending proposal on return and ignores the old response',async()=>{
 const pending=wait(),target=row('target','客户数据平台一期');let attempts=0;
 const h=make({listOpportunities:async()=>pageOf([]),getOpportunityDetailOverview:async()=>++attempts===1?pending.promise:{id:'c',opportunities:[target]}});
 const suggestion={opportunityId:'target',name:target.name,amount:'80',hasContent:true,stageIndex:2};h.page.aiOpportunitySuggestion=suggestion;
 const first=h.page.loadOpportunities();await tick();h.page.onHide();pending.resolve({id:'c',opportunities:[row('target','旧响应')]});await first;
 assert.equal(h.page.aiOpportunitySuggestion,suggestion);assert.equal(h.page.data.selectedOpportunity,null);h.page.onShow();await tick();
 assert.equal(h.page.data.selectedOpportunity.name,target.name);assert.equal(h.page.data.opportunityDraft.amount,'80');assert.equal(h.page.data.opportunityAIRecognized,true);assert.equal(h.page.aiOpportunitySuggestion,null);
 h.page.onHide();h.page.onShow();await tick();assert.equal(h.page.data.opportunityDraft.amount,'80');assert.equal(h.page.data.opportunityAIRecognized,true);
});
test('manual choice consumes a pending AI target and late lookup cannot replace the manual draft',async()=>{
 const pending=wait();let attempts=0;
 const h=make({listOpportunities:async()=>pageOf([row('manual')]),getOpportunityDetailOverview:async()=>{if(++attempts===1)throw Error('网络中断');return pending.promise;}});
 h.page.aiOpportunitySuggestion={opportunityId:'target',name:'AI 商机',amount:'80',hasContent:true,stageIndex:2};await h.page.loadOpportunities();
 const retry=h.page.retryOpportunities();await tick();h.page.chooseOpportunity({currentTarget:{dataset:{id:'manual'}}});h.page.changeOpportunityForm({detail:{form:{name:'人工商机',amount:'23',stageIndex:1}}});
 pending.resolve({id:'c',opportunities:[row('target')]});await retry;assert.equal(h.page.data.opportunityId,'manual');assert.equal(h.page.data.opportunityDraft.amount,'23');assert.equal(h.page.aiOpportunitySuggestion,null);assert.equal(h.page.data.opportunityAIRecognized,false);
 await h.page.loadOpportunities();assert.equal(h.page.data.opportunityId,'manual');assert.equal(h.page.data.opportunityDraft.amount,'23');assert.equal(attempts,2);
});
test('a saved manual draft takes priority when the pending AI target resolves on retry',async()=>{
 let attempts=0;const h=make({listOpportunities:async()=>pageOf([]),getOpportunityDetailOverview:async()=>{if(++attempts===1)throw Error('网络中断');return {id:'c',opportunities:[row('target')]};}});
 h.page.aiOpportunitySuggestion={opportunityId:'target',name:'AI 商机',amount:'80',hasContent:true,stageIndex:2};await h.page.loadOpportunities();
 const manual={name:'人工草稿',amount:'23',stageIndex:1};h.page.changeOpportunityForm({detail:{form:manual}});await h.page.retryOpportunities();assert.equal(h.page.data.opportunityDraft,manual);assert.equal(h.page.data.selectedOpportunity.id,'target');assert.equal(h.page.aiOpportunitySuggestion,null);assert.equal(h.page.data.opportunityAIRecognized,false);
});
test('mismatched exact customer is rejected while keeping the requested ID for explicit correction',async()=>{
 const h=make({listOpportunities:async()=>pageOf([]),getOpportunityDetailOverview:async()=>({id:'other',opportunities:[row('target')]})});h.page.data.opportunityId='target';await h.page.loadOpportunities();assert.equal(h.page.data.opportunityId,'target');assert.equal(h.page.data.selectedOpportunity,null);assert.match(h.page.data.opportunityError,/不属于当前客户/);
});
test('typing invalidates old search before debounce and keeps selected item across search pages',async()=>{
 const pending=[];const h=make({listOpportunities:()=>{const d=wait();pending.push(d);return d.promise;}});h.page.data.selectedOpportunity=row('chosen');h.page.data.opportunityId='chosen';
 h.page.searchOpportunities({detail:{value:'旧'}});h.flush();h.page.searchOpportunities({detail:{value:'新'}});pending[0].resolve(pageOf([row('stale')]));await tick();assert.equal(h.page.data.opportunityItems.length,0);h.flush();pending[1].resolve(pageOf([row('fresh')]));await tick();assert.equal(h.page.data.opportunityItems[0].id,'fresh');assert.equal(h.page.data.opportunityOptions[h.page.data.opportunityIndex].id,'chosen');
});
test('load-more failure keeps existing options and retry uses the same next offset',async()=>{
 const offsets=[];let fail=true;const h=make({listOpportunities:async x=>{offsets.push(x.offset);if(x.offset&&fail){fail=false;throw Error('网络中断');}return x.offset?pageOf([row('last')]):pageOf([row('first')],true,20);}});
 await h.page.loadOpportunities();await h.page.moreOpportunities();assert.equal(h.page.data.opportunityItems.length,1);assert.match(h.page.data.opportunityError,/网络中断/);await h.page.retryOpportunities();assert.deepEqual(offsets,[0,20,20]);assert.equal(h.page.data.opportunityItems.length,2);
});
test('late success/error cannot write after customer change, hide, or permission change',async()=>{
 for(const change of [h=>{h.page.data.customerId='other';},h=>h.page.onHide(),h=>{h.app.globalData.session.permissionVersion=2;}])for(const reject of [false,true]){
  const d=wait(),h=make({listOpportunities:()=>d.promise});const loaded=h.page.loadOpportunities();change(h);reject?d.reject(Error('late')):d.resolve(pageOf([row('late')]));await loaded;assert.equal(h.page.data.opportunityItems.length,0);assert.equal(h.page.data.opportunityError,'');
 }
});
test('FDE remains on its dedicated eligibility picker without fetching sales opportunities',async()=>{
 let reads=0;const h=make({listOpportunities:async()=>{reads++;return pageOf([]);}});h.page.data.isFde=true;await h.page.loadOpportunities();assert.equal(reads,0);assert.equal(h.page.data.fdeOpportunityVerified,false);
});

const choose=(page,id)=>page.chooseOpportunity({currentTarget:{dataset:{id}}});
const draft=(page,form)=>page.changeOpportunityForm({detail:{form,customerId:page.data.customerId,opportunityId:page.data.opportunityId}});
test('existing form changes never rename the new entry; new form starts without existing values',async()=>{
 const {page}=make({listOpportunities:async()=>pageOf([row('old','已有商机')])});await page.loadOpportunities();
 choose(page,'old');draft(page,{name:'已有商机',amount:'80'});
 assert.equal(page.opportunityOptionsFor([]).find(x=>x.id==='__new__').name,'本次拜访产生新商机');
 assert.equal(page.data.opportunityOptions.at(-1).name,'本次拜访产生新商机');
 page.toggleOpportunityPicker();choose(page,'__new__');assert.equal(page.data.opportunityId,'old');
 page.confirmOpportunityPicker();assert.equal(page.data.opportunityId,'__new__');assert.equal(page.data.selectedOpportunity,null);assert.equal(page.data.opportunityDraft,null);
});
test('new and existing drafts survive round trips independently, including no association',async()=>{
 const {page}=make({listOpportunities:async()=>pageOf([row('old')])});await page.loadOpportunities();
 choose(page,'__new__');draft(page,{name:'新项目',amount:'12',quarters:[{year:2026,quarter:4,collection:'3'}]});
 choose(page,'old');draft(page,{name:'旧项目修改',amount:'80'});choose(page,'');
 assert.equal(page.data.opportunityDraft,null);choose(page,'__new__');
 assert.equal(page.data.opportunityDraft.name,'新项目');assert.equal(page.data.opportunityDraft.amount,'12');assert.equal(page.data.opportunityDraft.quarters[0].collection,'3');
 assert.equal(page.data.opportunityOptions.at(-1).name,'本次拜访产生新商机');
 choose(page,'old');assert.equal(page.data.opportunityDraft.name,'旧项目修改');assert.equal(page.data.opportunityDraft.amount,'80');
});
test('drawer cancellation and re-confirming the same item leave the active draft unchanged',async()=>{
 const {page}=make({listOpportunities:async()=>pageOf([row('old')])});await page.loadOpportunities();choose(page,'__new__');
 draft(page,{name:'手写新商机',amount:'10'});const before=page.data.opportunityDraft;
 page.toggleOpportunityPicker();choose(page,'old');assert.equal(page.data.opportunityPickerOpen,true);assert.equal(page.data.opportunityDraft,before);
 page.closeOpportunityPicker();assert.equal(page.data.opportunityId,'__new__');assert.equal(page.data.selectedOpportunity,null);
 page.toggleOpportunityPicker();assert.equal(page.data.opportunityPendingId,'__new__');page.confirmOpportunityPicker();assert.equal(page.data.opportunityDraft,before);
 page.toggleOpportunityPicker();choose(page,'old');page.onHide();assert.equal(page.data.opportunityPickerOpen,false);assert.equal(page.data.opportunityId,'__new__');
});
test('pending drawer choice survives search and pagination until explicit confirmation',async()=>{
 const {page,flush}=make({listOpportunities:async x=>x.query?pageOf([row('other')]):pageOf([row('target')])});await page.loadOpportunities();
 page.toggleOpportunityPicker();choose(page,'target');page.searchOpportunities({detail:{value:'other'}});flush();await tick();
 assert.equal(page.data.opportunityId||'','');assert.equal(page.data.opportunityPendingId,'target');assert.ok(page.data.opportunityOptions.some(x=>x.id==='target'));
 page.confirmOpportunityPicker();assert.equal(page.data.opportunityId,'target');assert.equal(page.data.selectedOpportunity.id,'target');
});
test('late form events and customer switches cannot leak another opportunity draft',async()=>{
 const {page}=make({listOpportunities:async()=>pageOf([row('old')])});await page.loadOpportunities();choose(page,'old');draft(page,{name:'旧项目'});choose(page,'__new__');
 page.changeOpportunityForm({detail:{customerId:'c',opportunityId:'old',form:{name:'迟到旧项目'}}});assert.equal(page.data.opportunityDraft,null);
 draft(page,{name:'新项目'});page.changeOpportunityForm({detail:{customerId:'other',opportunityId:'__new__',form:{name:'另一客户'}}});assert.equal(page.data.opportunityDraft.name,'新项目');
 page.searchCustomers=()=>{};page.changeCustomer();assert.equal(Object.keys(page.data.opportunityDrafts).length,0);assert.equal(page.data.opportunityDraft,null);
});
test('the actual shared form resets existing values and restores only the draft for the selected mode',async()=>{
 const {page}=make({listOpportunities:async()=>pageOf([row('old','已有商机')])});await page.loadOpportunities();
 const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');let definition;
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:d=>definition=d,require:n=>n.endsWith('/apiClient')?{}:require(path.resolve(path.dirname(file),n)),clearTimeout});
 const form={...definition.methods,data:JSON.parse(JSON.stringify(definition.data)),properties:{customerId:'c',visitContext:true},
   setData:v=>Object.assign(form.data,v),triggerEvent:(_,detail)=>page.changeOpportunityForm({detail})};
 function reset(){Object.assign(form.properties,{existing:page.data.selectedOpportunity,savedDraft:page.data.opportunityDraft});form.reset();}
 choose(page,'old');reset();assert.equal(page.data.opportunityDraft.name,'已有商机');
 choose(page,'__new__');reset();assert.equal(page.data.opportunityDraft.name,'');
 draft(page,{...form.data.form,name:'新项目'});choose(page,'old');reset();assert.equal(page.data.opportunityDraft.name,'已有商机');
 choose(page,'__new__');reset();assert.equal(page.data.opportunityDraft.name,'新项目');
});
