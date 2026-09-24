require('./helpers/business-options');
const test=require('node:test'); const assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm'); const opp=require('../miniprogram/utils/opportunity');
function form(api, wx={}) { let definition; vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/components/opportunity-form/index.js','utf8'),{require:n=>n.includes('apiClient')?api:n.includes('businessOptions')?require('../miniprogram/utils/businessOptions'):n.endsWith('/access')?require('../miniprogram/utils/access'):opp,Component:d=>{definition=d},setTimeout,clearTimeout,wx}); return {...definition.methods,properties:{customerId:'c',existing:null},data:{...definition.data,form:{...opp.formFor(null),name:'客服助手'}},setData(o){for(const [key,value] of Object.entries(o)){const parts=key.split('.');let target=this.data;while(parts.length>1)target=target[parts.shift()];target[parts[0]]=value;}},triggerEvent(){}}; }
test('共享表单名称检查忽略迟到结果，编辑排除自己',async()=>{const pending=[];const p=form({checkOpportunityName:(...args)=>new Promise(resolve=>pending.push({args,resolve}))});const a=p.checkName();p.data.form.name='客服助手二期';p.properties.existing={id:'o'};const b=p.checkName();pending[1].resolve({available:true});assert.equal(await b,true);pending[0].resolve({available:false,message:'重名'});assert.equal(await a,false);assert.equal(p.data.error,'');assert.equal(pending[1].args[2],'o');});
test('重名和网络失败阻止两个入口共用的提交准备',async()=>{const p=form({checkOpportunityName:async()=>({available:false,message:'该客户已有同名商机'})});assert.equal(await p.checkName(),false);assert.match(p.data.error,/同名/);const q=form({checkOpportunityName:async()=>{throw Error('offline')}});assert.equal(await q.checkName(),false);assert.match(q.data.error,/重试/);});
test('取消关闭确认不会生成保存请求',async()=>{const p=form({checkOpportunityName:async()=>({available:true})},{showModal:o=>o.success({confirm:false})});p.data.form={...p.data.form,amount:'5',stageIndex:5,expected_close_date:'2026-12-01',quarters:[{year:2026,quarter:4,recognized:'5',collection:'5'}]};await assert.rejects(p.prepare(),/已取消/);});
test('金额万元转元且空与零不同，分季度内容不会混写',()=>{const f=opp.formFor(null);Object.assign(f,{name:'商机',amount:'12.3456',stageIndex:0,expected_close_date:'2026-12-01',quarters:[{year:2026,quarter:3,recognized:'0',collection:''},{year:2026,quarter:4,recognized:'10',collection:'8'}]});const p=opp.payload(f,null);assert.equal(p.amount,123456);assert.equal(p.quarterly_forecasts[0].recognized_amount,0);assert.equal(p.quarterly_forecasts[0].collection_amount,null);assert.equal(p.quarterly_forecasts[1].recognized_amount,100000);assert.throws(()=>opp.payload({...f,stageIndex:-1},null),/阶段/);assert.throws(()=>opp.amount('-1'),/非负/);});
test('商机等级按 ACV 边界自动划分，缺失金额不误判为 D',()=>{assert.equal(opp.gradeOfAmount(1000000).code,'A');assert.equal(opp.gradeOfAmount(999999).code,'B');assert.equal(opp.gradeOfAmount(500000).code,'B');assert.equal(opp.gradeOfAmount(499999).code,'C');assert.equal(opp.gradeOfAmount(100000).code,'C');assert.equal(opp.gradeOfAmount(99999).code,'D');assert.equal(opp.gradeOfAmount(null),null);assert.equal(opp.gradeOfAmount(''),null);});
test('赢单、丢单和重新打开必须明确确认，Lost不伪造0%进展',()=>{assert.equal(opp.stageOf({status:'lost',probability:50}).probability,null);assert.equal(opp.stageOf({status:'won',probability:100}).text,'赢单 Won－100%');assert.equal(opp.needsConfirmation({status:'lost'},{status:'open'}),'close');assert.equal(opp.needsConfirmation({status:'open'},{status:'won'}),'reopen');assert.equal(opp.needsConfirmation({status:'won'},{status:'won'}),'');});

test('父页面切换保存状态不覆盖刚选择的阶段，更换客户才重置',async()=>{
 const p=form({checkOpportunityName:async()=>({available:true})});
 p.properties.existing={id:'o',sales_channel:'direct',name:'客服助手',amount:50000,probability:30,status:'open',expected_close_date:'2026-12-01',version_no:1,quarterly_forecasts:[{year:2026,quarter:4,recognized_amount:0,collection_amount:0}]};
 p.reset();p.stage({detail:{value:2}});
 p.properties.existing={...p.properties.existing};p.reset();
 assert.equal((await p.prepare()).probability,50);
 p.properties.customerId='other';p.properties.existing=null;p.reset();
 assert.equal(p.data.form.stageIndex,-1);assert.equal(p.data.form.name,'');
});
test('旧后端缺少变更回执不能显示内容未变化或跳转成功',async()=>{
 let definition;const toasts=[];let navigated=false;
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/opportunity-create/index.js','utf8'),{
  require:()=>({createOpportunity:async()=>({id:'o'})}),Page:d=>{definition=d},
  wx:{showToast:r=>toasts.push(r),setStorageSync(){},navigateBack(){navigated=true}},setTimeout,clearTimeout,
 });
 const context={...definition,data:{...definition.data,customerId:'c'},setData(o){Object.assign(this.data,o)},selectComponent(){return {prepare:async()=>({})}}};
 await context.submit();assert.match(context.data.error,/未收到完整保存回执/);assert.equal(toasts.length,0);assert.equal(navigated,false);assert.equal(context.data.busy,false);
});

test('新增商机页沿用全局卡片与渐变视觉且保留关键交互',()=>{
 const pageWxml=fs.readFileSync(__dirname+'/../miniprogram/pages/opportunity-create/index.wxml','utf8');
 const pageWxss=fs.readFileSync(__dirname+'/../miniprogram/pages/opportunity-create/index.wxss','utf8');
 const formWxss=fs.readFileSync(__dirname+'/../miniprogram/components/opportunity-form/index.wxss','utf8');
 assert.match(pageWxml,/class="opportunity-create-hero"/);
 assert.match(pageWxml,/class="surface customer-picker-card"/);
 assert.match(pageWxml,/class="surface form-card"/);
 assert.match(pageWxml,/bindinput="inputCustomer"/);
 assert.match(pageWxml,/bindtap="selectCustomer"/);
 assert.match(pageWxml,/bindtap="submit"/);
 assert.match(pageWxss,/\.opportunity-create-hero\{[^}]*linear-gradient/);
 assert.match(pageWxss,/\.submit\{[^}]*linear-gradient/);
 assert.match(formWxss,/\.control\{[^}]*border-radius:17rpx/);
 assert.match(formWxss,/\.fold\{[^}]*border-radius:17rpx/);
});

function validForm(stageIndex, quarters=[]) { return {...opp.formFor(null),name:'新商机',amount:'50',stageIndex,expected_close_date:'2026-12-01',quarters}; }
test('伙伴搜索迟到结果不能覆盖新搜索，更换客户清空旧选择',async()=>{
 const pending=[];const p=form({listPartners:()=>new Promise(resolve=>pending.push(resolve))});p.reset();
 p.data.partnerQuery='旧';const old=p.loadPartners();p.data.partnerQuery='新';const fresh=p.loadPartners();
 pending[1]({items:[{id:'new',name:'新伙伴'}],total:1});await fresh;
 pending[0]({items:[{id:'old',name:'旧伙伴'}],total:1});await old;
 assert.equal(p.data.partners[0].id,'new');p.choosePartner({currentTarget:{dataset:{id:'new'}}});assert.equal(p.data.form.partner_id,'new');
 p.properties.customerId='other-customer';p.reset();assert.equal(p.data.form.partner_id,null);assert.equal(p.data.partners.length,0);
});
test('10%可不填或只填一项；30%到100%均拦截缺失预测，明确零值可通过',()=>{
 assert.deepEqual(opp.payload(validForm(0),null).quarterly_forecasts,[]);
 assert.equal(opp.payload(validForm(0,[{year:2026,quarter:3,recognized:'0',collection:''}]),null).quarterly_forecasts[0].collection_amount,null);
 for (const stageIndex of [1,2,3,4,5]) {
  for (const quarters of [[],[{year:2026,quarter:3,recognized:' ',collection:null}],[{year:2026,quarter:3,recognized:'0',collection:''}],[{year:2026,quarter:3,recognized:undefined,collection:'2'}]]) {
   assert.throws(()=>opp.payload(validForm(stageIndex,quarters),null),e=>e.code==='FORECAST_REQUIRED');
  }
  const p=opp.payload(validForm(stageIndex,[{year:2026,quarter:3,recognized:'0',collection:'0'}]),null);
  assert.equal(p.quarterly_forecasts[0].recognized_amount,0);assert.equal(p.quarterly_forecasts[0].collection_amount,0);
 }
 assert.doesNotThrow(()=>opp.payload(validForm(6),null));
});
test('多季度不得用完整季度掩盖另一季度单项缺失，全部空白季度不强制填写',()=>{
 const rows=[{year:2026,quarter:3,recognized:'10',collection:'8'},{year:2026,quarter:4,recognized:'0',collection:''}];
 assert.throws(()=>opp.payload(validForm(1,rows),null),e=>e.forecastQuarter.quarter===4 && /回款/.test(e.message));
 rows[1].recognized='';assert.doesNotThrow(()=>opp.payload(validForm(1,rows),null));
 rows[0].recognized='-1';assert.throws(()=>opp.payload(validForm(1,rows),null),/非负/);
});
test('升到30%自动展开必填，降回10%保留草稿并恢复选填',()=>{
 const p=form({});p.reset();p.stage({detail:{value:1}});
 assert.equal(p.data.forecastRequired,true);assert.equal(p.data.showForecast,true);
 p.forecast({currentTarget:{dataset:{key:'recognized'}},detail:{value:'0'}});
 p.stage({detail:{value:0}});assert.equal(p.data.forecastRequired,false);assert.equal(p.data.form.quarters[0].recognized,'0');
 p.properties.savedDraft=validForm(2);p.properties.customerId='other';p.reset();assert.equal(p.data.forecastRequired,true);assert.equal(p.data.showForecast,true);
});
test('提交不完整预测时定位问题季度且在名称请求前阻止提交',async()=>{
 let called=false;const p=form({checkOpportunityName:async()=>{called=true;return{available:true}}});p.reset();
 p.data.form=validForm(1,[{year:2026,quarter:4,recognized:'3',collection:''}]);p.data.showForecast=false;
 await assert.rejects(p.prepare(),/2026 Q4.*回款/);
 assert.equal(called,false);assert.equal(p.data.showForecast,true);assert.equal(p.data.quarterLabel,'2026 Q4');assert.equal(p.data.recognized,'3');assert.match(p.data.forecastError,/回款/);
});
test('季度预测按阶段概率计算，零、空、无阶段与金额精度区分',()=>{
 for(const [index,expected] of [[0,'10'],[1,'30'],[2,'50'],[3,'70'],[4,'90'],[5,'100']])assert.equal(opp.weightedQuarterAmount('100',index),expected);
 assert.equal(opp.weightedQuarterAmount('80',1),'24');
 assert.equal(opp.weightedQuarterAmount('0',1),'0');
 for(const value of ['',null,undefined,'bad','-1'])assert.equal(opp.weightedQuarterAmount(value,1),'—');
 assert.equal(opp.weightedQuarterAmount('100',-1),'—');assert.equal(opp.weightedQuarterAmount('100',6),'—');
 assert.equal(opp.weightedQuarterAmount('0.0001',1),'0.00003');
});
test('改金额、切阶段、切季度和恢复草稿同步更新预测，提交保留原始金额',async()=>{
 const p=form({checkOpportunityName:async()=>({available:true})});p.reset();
 p.data.form=validForm(1);p.loadQuarter();
 const fill=(key,value)=>p.forecast({currentTarget:{dataset:{key}},detail:{value}});
 fill('collection','100');fill('recognized','80');assert.equal(p.data.predictedCollection,'30');assert.equal(p.data.predictedRecognized,'24');
 p.stage({detail:{value:2}});assert.equal(p.data.predictedCollection,'50');assert.equal(p.data.predictedRecognized,'40');
 const old=p.data.quarterIndex;const next=old+1;p.quarter({detail:{value:next}});assert.equal(p.data.predictedCollection,'—');assert.equal(p.data.predictedRecognized,'—');
 fill('collection','20');fill('recognized','0');assert.equal(p.data.predictedCollection,'10');assert.equal(p.data.predictedRecognized,'0');
 p.quarter({detail:{value:old}});assert.equal(p.data.collection,'100');assert.equal(p.data.predictedCollection,'50');
 const payload=await p.prepare();assert.equal(payload.quarterly_forecasts[0].collection_amount,1000000);assert.equal(payload.quarterly_forecasts[0].recognized_amount,800000);
 p.properties.savedDraft=JSON.parse(JSON.stringify(p.data.form));p.properties.customerId='restore';p.reset();assert.equal(p.data.predictedCollection,'50');assert.equal(p.data.predictedRecognized,'40');
});

test('新商机默认直销，合作伙伴必须有目录身份，旧伙伴名称保留待匹配',()=>{
 const fresh=validForm(0);assert.equal(fresh.partner_name,'直销');assert.equal(fresh.partner_mode,'direct');
 assert.equal(opp.payload(fresh,null).sales_channel,'direct');assert.equal(opp.payload(fresh,null).partner_id,null);
 assert.throws(()=>opp.payload({...fresh,partner_mode:'partner',partner_name:'  '},null),/合作伙伴/);
 assert.throws(()=>opp.payload({...fresh,partner_mode:'partner',partner_name:'直销'},null),/合作伙伴/);
 assert.throws(()=>opp.payload({...fresh,partner_mode:'partner',partner_name:'伙伴甲'},null),/已有合作伙伴/);
 assert.equal(opp.payload({...fresh,partner_mode:'partner',partner_name:'伙伴甲',partner_id:'partner-id'},null).partner_id,'partner-id');
 const old=opp.formFor({name:'旧商机',partner_name:'伙伴乙'});assert.equal(old.partner_mode,'partner');assert.equal(old.partner_name,'伙伴乙');
});
test('直销与合作伙伴切换会更新草稿，禁用期间不能切换',()=>{
 const c=form({});c.reset();assert.equal(c.data.partnerIndex,0);
 c.partnerMode({detail:{value:'1'}});assert.equal(c.data.form.partner_name,'');assert.equal(c.data.form.partner_mode,'partner');
 c.input({currentTarget:{dataset:{key:'partner_name'}},detail:{value:'伙伴甲'}});
 c.partnerMode({detail:{value:'0'}});assert.equal(c.data.form.partner_name,'直销');
 c.properties.disabled=true;c.partnerMode({detail:{value:'1'}});assert.equal(c.data.form.partner_mode,'direct');
});
test('草稿恢复保留未填完的合作伙伴选择，旧空草稿等待确认渠道',()=>{
 const c=form({});c.properties.savedDraft={...validForm(0),partner_mode:'partner',partner_name:''};c.reset();
 assert.equal(c.data.partnerIndex,1);assert.equal(c.data.form.partner_name,'');
 const legacy=form({});const draft=validForm(0);delete draft.partner_mode;draft.partner_name='';legacy.properties.savedDraft=draft;legacy.reset();
 assert.equal(legacy.data.partnerIndex,0);assert.equal(legacy.data.form.partner_name,'');assert.equal(legacy.data.form.partner_mode,'unknown');assert.throws(()=>opp.payload(legacy.data.form,null),/确认销售渠道/);
});


test('拜访表单不带入商机历史 FDE，只恢复本次草稿并独立保留商机编辑语义', async()=>{
 const row={id:'o',name:'客服助手',amount:50000,probability:10,status:'open',sales_channel:'direct',expected_close_date:'2026-12-01',version_no:1,fde_members:[{id:'zhou',name:'周玮'}]};
 const p=form({checkOpportunityName:async()=>({available:true})});p.properties.existing=row;p.properties.visitContext=true;p.reset();
 assert.equal(p.data.form.visit_fde_members.length,0);assert.equal(p.data.form.fde_member_ids,undefined);
 p.fdeChanged({detail:{members:[{id:'zhang',name:'张家涛'}],memberIds:['zhang']}});
 p.properties.existing={...row};p.reset();assert.equal(p.data.form.visit_fde_members[0].id,'zhang');
 const payload=await p.prepare();assert.equal(payload.fde_member_ids,undefined);
 const restored=opp.formForVisit(row,p.data.form);assert.deepEqual(restored.visit_fde_member_ids,['zhang']);
 const legacy=opp.formForVisit(row,opp.formFor(row));assert.equal(legacy.visit_fde_members.length,0);
 assert.deepEqual(opp.payload(opp.formFor(row),row).fde_member_ids,['zhou']);
 p.properties.existing={...row,id:'other'};p.properties.savedDraft=null;p.reset();assert.equal(p.data.form.visit_fde_members.length,0);
});
