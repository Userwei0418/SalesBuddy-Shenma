require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const opp=require('../miniprogram/utils/opportunity'),extra=require('../miniprogram/utils/opportunityExtraFields');
const clone=x=>JSON.parse(JSON.stringify(x));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const enumValues={priority:['p0'],trial_status:['preparing','requested','enabled'],ppl_status:['created','not_created'],external_system_status:['commit','high','medium','low'],fde_support_stage:['trial_support','demo_build','project_delivery'],customer_source:['company_lead','partner_existing','marketing_event','customer_referral','partner_stock','digital_marketing','sales_outbound']};
const keys=[...extra.KEYS,'fde_member_ids'];
function catalog(){return {definition_version:'opportunity_workspace_v2',participant_roles:[{value:'sales',label:'销售'},{value:'fde',label:'FDE'},{value:'other',label:'其他'}],fields:keys.map(key=>({key,label:key,type:key==='customer_source'?'multi_enum':enumValues[key]?'enum':key==='is_key'?'boolean':key==='participants'||key==='fde_member_ids'?'people':key==='primary_fde_id'?'person':'text',read_only:false,editable:true,...(enumValues[key]?{options:enumValues[key].map(value=>({value,label:value}))}:{})}))};}
function record(overrides={}){return {id:'o',name:'客服助手',amount:50000,probability:10,status:'open',expected_close_date:'2027-01-01',sales_channel:'direct',version_no:7,quarterly_forecasts:[],fde_members:[],capabilities:{editable_fields:keys,can_manage_fde_members:true},...overrides};}
function valid(row=null){return {...opp.formFor(row),name:'客服助手',amount:'5',stageIndex:0,expected_close_date:'2027-01-01',partner_mode:'direct'};}
function dirty(form,values){form.extra_values={...form.extra_values,...values};form.extra_dirty={...form.extra_dirty,...Object.fromEntries(Object.keys(values).map(k=>[k,true]))};return form;}
const event=(key,value)=>({currentTarget:{dataset:{key}},detail:{value}});
function harness({row=null,visit=false,request}={}){
 const requests=[];const app={globalData:{session:{workspaceId:'w',userId:'u',loginAt:'1',permissions:{'opportunity.create':true,'opportunity.update':true,'opportunity.fde_members':true}}}};
 const api={request:async options=>{requests.push(options);return request?request(options):catalog();},checkOpportunityName:async()=>({available:true}),getOpportunityCreateOptions:async()=>({teams:[{id:'t',name:'本组'}],default_team_id:'t'}),getOpportunityMaterials:async()=>({opportunity_id:'o',version_no:7,items:[],retired_items:[]})};
 let definition;const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:d=>definition=d,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>app,wx:{showModal:o=>o.success({confirm:true})},setTimeout,clearTimeout});
 const c={...definition.methods,properties:{customerId:'c',existing:row,visitContext:visit,disabled:false,savedDraft:null},data:clone(definition.data),setData(patch){for(const [key,value]of Object.entries(patch)){const parts=key.split('.');let target=this.data;while(parts.length>1){const part=parts.shift();if(!target[part])target[part]={};target=target[part];}target[parts[0]]=value;}},triggerEvent(){}};
 c.reset();c.data.form=visit?{...opp.formForVisit(row),...valid(row),visit_fde_members:[],visit_fde_member_ids:[]}:valid(row);c.data.requiresOwnerTeam=false;c.data.ownerTeamsLoading=false;
 return {c,app,requests,api};
}

test('新建和旧记录未改补充字段不增加null/空数组或v2标识',()=>{
 for(const row of [null,record(),record({summary:'旧总结',is_key:false,customer_source:['legacy'],participants:[{user_id:'inactive',role:'other',name:'停用成员',is_active:false}]})]){
  const form=valid(row),payload=opp.payload(form,row);
  for(const key of extra.KEYS)assert.equal(Object.hasOwn(payload,key),false,key);
  assert.equal(Object.hasOwn(payload,'fields_schema_version'),false);
  if(row)assert.equal(Object.hasOwn(payload,'fde_member_ids'),false,'unchanged persistent roster must be omitted');
 }
});
test('明确false/null/空数组保留语义，人物写值剔除所有显示字段',()=>{
 const form=dirty(valid(),{is_key:false,summary:'  ',priority:null,customer_source:[],participants:[{user_id:'u1',role:'sales',name:'显示名称',is_active:false,teams:['t']}]});
 const p=opp.payload(form,null);assert.equal(p.fields_schema_version,2);assert.equal(p.is_key,false);assert.equal(p.summary,null);assert.equal(p.priority,null);assert.deepEqual(p.customer_source,[]);assert.deepEqual(p.participants,[{user_id:'u1',role:'sales'}]);
 assert.equal(form.extra_values.summary,'  ','payload normalization must not overwrite draft');
});
for(const [key,value]of [['is_key','false'],['customer_source',null],['customer_source',['a','a']],['participants',null],['participants',[{user_id:'a',role:'admin'}]],['summary','长'.repeat(501)]])test(`非法可选值在单次保存前拦截 ${key} ${typeof value}`,()=>{assert.throws(()=>opp.payload(dirty(valid(),{[key]:value}),null));});
test('主责显式包含长期名单，移除主责需同时清空/更换',()=>{
 const form=dirty(valid(),{primary_fde_id:'f1'});assert.throws(()=>opp.payload(form,null),/长期协助/);
 form.extra_roster=[{id:'f1',name:'FDE一'},{id:'f2',name:'FDE二'}];assert.deepEqual(opp.payload(form,null).fde_member_ids,['f1','f2']);
 form.extra_dirty={fde_member_ids:true};form.extra_roster=[{id:'f2'}];assert.throws(()=>opp.payload(form,null),/移除主责/);
 dirty(form,{primary_fde_id:null});const p=opp.payload(form,null);assert.equal(p.primary_fde_id,null);assert.deepEqual(p.fde_member_ids,['f2']);
});
test('拜访草稿保留显式新字段但不从长期名单推导本次出席',()=>{
 const row=record({primary_fde_id:'f1',fde_members:[{id:'f1',name:'FDE一'}]});const draft=dirty(valid(row),{note:'草稿'});draft.extra_roster.push({id:'f2',name:'FDE二'});draft.extra_dirty.fde_member_ids=true;draft.visit_fde_members=[{id:'attendee',name:'本次出席'}];
 const form=opp.formForVisit(row,draft);assert.equal(form.fde_member_ids,undefined);assert.deepEqual(form.visit_fde_member_ids,['attendee']);const p=opp.payload(form,row);assert.deepEqual(p.fde_member_ids,['f1','f2']);assert.equal(p.note,'草稿');assert.equal(p.fields_schema_version,2);
});
test('真实共享表单两个入口一次prepare同时包含核心和可选字段，拜访默认省略长期关系',async()=>{
 for(const visit of [false,true]){const row=record({fde_members:[{id:'f1',name:'FDE一'}]});const {c}=harness({row,visit});await c.loadExtraOptions();c.extraChange(event('summary','本次人工补充'));c.extraChange(event('is_key',false));const p=await c.prepare();assert.equal(p.name,'客服助手');assert.equal(p.summary,'本次人工补充');assert.equal(p.is_key,false);assert.equal(p.fields_schema_version,2);assert.equal(p.fde_member_ids,undefined);assert.equal(p.version_no,7);}
});
test('拜访显式长期名单+主责同一个quality mutation，本次出席仍独立',async()=>{
 const {c}=harness({row:record(),visit:true});await c.loadExtraOptions();c.data.form.visit_fde_member_ids=['attendee'];c.extraRosterChanged({detail:{members:[{id:'f1',name:'主责',is_active:true}],memberIds:['f1']}});c.extraPrimary({detail:{value:'f1'}});const p=await c.prepare();assert.equal(p.primary_fde_id,'f1');assert.deepEqual(p.fde_member_ids,['f1']);assert.equal(p.fields_schema_version,2);assert.deepEqual(c.data.form.visit_fde_member_ids,['attendee']);
});
test('显式清空长期名单和主责时，本次仍出席的同一人不能被客户端加回',async()=>{
 const {c}=harness({row:record({primary_fde_id:'f1',fde_members:[{id:'f1',name:'FDE一'}]}),visit:true});await c.loadExtraOptions();
 c.data.form.visit_fde_member_ids=['f1'];c.data.form.visit_fde_members=[{id:'f1',name:'FDE一'}];
 c.extraRosterChanged({detail:{members:[],memberIds:[]}});await assert.rejects(c.prepare(),/移除主责/);
 c.extraPrimary({detail:{value:null}});const p=await c.prepare();
 assert.deepEqual(p.fde_member_ids,[]);assert.equal(p.primary_fde_id,null);assert.equal(p.fields_schema_version,2);
 assert.deepEqual(c.data.form.visit_fde_member_ids,['f1']);assert.equal(p.version_no,7);
});
test('新字段动态选项，多选可取消历史失效值，false不会变空',async()=>{
 const {c}=harness({row:record({customer_source:['legacy'],is_key:false})});await c.loadExtraOptions();assert.equal(c.data.extraFields.find(f=>f.key==='is_key').value,false);assert.equal(c.data.extraFields.find(f=>f.key==='customer_source').options.at(-1).legacy,true);
 c.extraToggleSource({currentTarget:{dataset:{key:'customer_source',value:'legacy'}}});c.extraToggleSource({currentTarget:{dataset:{key:'customer_source',value:'company_lead'}}});assert.deepEqual((await c.prepare()).customer_source,['company_lead']);
 c.extraChange(event('priority','invented'));assert.equal(c.data.form.extra_dirty.priority,undefined);
});
test('全局禁用、逐字段权限和FDE权限不可由目录或已选人员扩大',async()=>{
 const row=record({capabilities:{editable_fields:['summary','participants'],can_manage_fde_members:false}});const {c}=harness({row});await c.loadExtraOptions();assert.equal(c.data.extraCanManageFde,false);c.extraChange(event('note','无权修改'));assert.equal(c.data.form.extra_dirty.note,undefined);c.properties.disabled=true;c.extraChange(event('summary','禁用'));assert.equal(c.data.form.extra_dirty.summary,undefined);c.openExtraMembers();assert.equal(c.data.extraMemberOpen,false);
 c.data.form=dirty(c.data.form,{note:'旧草稿越权'});await assert.rejects(c.prepare(),/不可编辑/);
});
test('旧接口选项404不会阻断未修改的核心保存，但新字段草稿不静默丢弃',async()=>{
 const {c}=harness({request:async()=>{throw Object.assign(Error('目录未就绪'),{statusCode:404});}});await c.loadExtraOptions();assert.match(c.data.extraError,/目录未就绪/);assert.equal((await c.prepare()).name,'客服助手');dirty(c.data.form,{summary:'未保存的补充'});await assert.rejects(c.prepare(),/更多信息/);assert.equal(c.data.form.extra_values.summary,'未保存的补充');
});
test('目录跨身份、权限变化和商机上下文迟到均不回填',async()=>{
 for(const change of ['identity','permission','source']){const pending=deferred();const {c,app}=harness({request:()=>pending.promise});const waiting=c.loadExtraOptions();if(change==='identity')app.globalData.session.userId='other';if(change==='permission')app.globalData.session.permissions['opportunity.update']=false;if(change==='source')c.sourceKey='other-record';pending.resolve(catalog());await waiting;assert.equal(c.data.extraReady,false,change);assert.deepEqual(c.data.extraFields,[]);}
});
test('保存草稿遇到身份切换即阻断；不为恢复草稿发第二次补丁写入',async()=>{
 const {c,app,requests}=harness();await c.loadExtraOptions();c.extraChange(event('summary','保留'));app.globalData.session.loginAt='next';await assert.rejects(c.prepare(),/重新加载/);assert.equal(c.data.form.extra_values.summary,'保留');assert.ok(requests.every(r=>(r.method||'GET')==='GET'));
});
test('参与者按目录分页搜索与职责选择，写值只有user_id/role，旧停用成员可移除',async()=>{
 const row=record({participants:[{user_id:'inactive',role:'other',name:'旧成员',is_active:false}]});const {c,requests}=harness({row,request:async o=>o.path==='/opportunities/field-options'?catalog():{items:[{id:'f1',name:'可选FDE',is_active:true,roles:['fde'],account_code:'FDE1'}],total:1,has_more:false,next_offset:null}});await c.loadExtraOptions();c.openExtraMembers();assert.equal(requests.length,1);await c.extraMemberRole({detail:{value:'fde'}});assert.match(requests.at(-1).path,/kind=fde/);c.chooseExtraMember({currentTarget:{dataset:{id:'f1'}}});const p=await c.prepare();assert.deepEqual(p.participants,[{user_id:'inactive',role:'other'},{user_id:'f1',role:'fde'}]);c.removeExtraMember({currentTarget:{dataset:{id:'inactive'}}});assert.deepEqual((await c.prepare()).participants,[{user_id:'f1',role:'fde'}]);
});
test('关闭人员弹层后迟到结果及停用目录数据均不成为可选项',async()=>{
 const pending=deferred();let directory=pending.promise;const {c}=harness({request:o=>o.path==='/opportunities/field-options'?Promise.resolve(catalog()):directory});await c.loadExtraOptions();c.openExtraMembers();const p=c.extraMemberRole({detail:{value:'other'}});c.closeExtraMembers();pending.resolve({items:[{id:'u',name:'迟到',is_active:true}],total:1,has_more:false,next_offset:null});await p;assert.equal(c.data.extraMemberRows.length,0);
 directory=Promise.resolve({items:[{id:'u',name:'停用',is_active:false}],total:1,has_more:false,next_offset:null});c.openExtraMembers();await c.extraMemberRole({detail:{value:'other'}});assert.match(c.data.extraMemberError,/不完整/);assert.equal(c.data.extraMemberRows.length,0);
});

test('独立真实保存页409只提交一次核心+补充信息，保留草稿供核对',async()=>{
 const {c,app}=harness({row:record()});await c.loadExtraOptions();c.extraChange(event('summary','待保存内容'));const posts=[];let definition;
 const file=path.resolve(__dirname,'../miniprogram/pages/opportunity-create/index.js');const api={createOpportunity:async(customer,payload)=>{posts.push(payload);throw Object.assign(Error('版本已变化，请重新核对'),{statusCode:409});},getCompanyPresentation:async()=>({opportunity_amount:{definition:{schema_version:1,warning_threshold_wan:1000}}}),getPendingOpportunitySave:()=>null,canRetryOpportunitySave:()=>false};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:d=>definition=d,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>app,wx:{showToast(){},setStorageSync(){},showModal:o=>o.success({confirm:true})},setTimeout,clearTimeout});
 const page={...definition,data:{...definition.data,customerId:'c',existing:c.properties.existing,loading:false},setData(patch){Object.assign(this.data,patch);},selectComponent:()=>c};
 await page.submit();assert.equal(posts.length,1);assert.equal(posts[0].summary,'待保存内容');assert.equal(posts[0].name,'客服助手');assert.equal(posts[0].version_no,7);assert.equal(posts[0].fields_schema_version,2);assert.match(page.data.error,/版本已变化/);assert.equal(c.data.form.extra_values.summary,'待保存内容');assert.equal(page.data.busy,false);
});

test('普通长期FDE选择也标v2且移除主责必须先更换，未改名单不顺带提交',async()=>{
 const {c}=harness({row:record({primary_fde_id:'f1',fde_members:[{id:'f1',name:'主责'}]})});await c.loadExtraOptions();c.extraChange(event('summary','只改总结'));assert.equal((await c.prepare()).fde_member_ids,undefined);
 c.fdeChanged({detail:{members:[],memberIds:[]}});await assert.rejects(c.prepare(),/移除主责/);c.extraPrimary({detail:{value:null}});const p=await c.prepare();assert.deepEqual(p.fde_member_ids,[]);assert.equal(p.fields_schema_version,2);
});

test('历史主责缺目录详情时显示已设置，不误示未设置也不自动清空',async()=>{
 const {c}=harness({row:record({primary_fde_id:'legacy',primary_fde:{id:'legacy',name:'历史主责',is_active:false}})});await c.loadExtraOptions();const option=c.data.extraPrimaryOptions.find(p=>p.value==='legacy');assert.equal(option.label,'历史主责');assert.equal(option.disabled,true);assert.equal((await c.prepare()).primary_fde_id,undefined);
});
