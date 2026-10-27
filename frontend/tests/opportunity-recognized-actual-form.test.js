const test = require('node:test');
const assert = require('node:assert/strict');
const form = require('../miniprogram/utils/opportunityRecognizedActualForm');
const errors = require('../miniprogram/utils/errorMessages');
const period = {year:2026,quarter:4,label:'2026 Q4'};
const item = (id, amount = null, patch = {}) => ({material_line_id:id,material_code:id,material_name:`物料${id}`,is_active:true,eligible:true,
  amount,status:amount===null?'missing':'confirmed',source_ref:amount===null?'':'原依据',note:'',...patch});
const response = (patch = {}) => ({opportunity_id:'opportunity',period:{year:2026,quarter:4},can_edit:true,
  actual:{amount:null,known_subtotal:null,status:'missing',source:null,revision:'a'.repeat(64),requires_source_replacement:false,attribution_status:'not_registered',missing_material_count:2},
  items:[item('one'),item('two')],editable_reason:null,...patch});
const send = (draft, result) => form.payload({'2026-Q4':draft},{'2026-Q4':result});
function edited(result, id, fields) {const d=form.draftFor(result);d.rows[id]={...d.rows[id],...fields};return d;}

test('按物料累计替换，精确金额与零可保存，未改动行及未填季度不重写',()=>{
  const r=response({items:[item('one','100000.00'),item('two')]});
  assert.deepEqual(send(form.draftFor(r),r),[]);
  const result=send(edited(r,'one',{amount:'15.000001',evidence:'验收单'}),r)[0];
  assert.deepEqual(result.entries,[{material_line_id:'one',amount:'150000.01',source_ref:'验收单',note:''}]);
  assert.equal(result.expected_actual_revision,'a'.repeat(64));
  assert.equal(send(edited(r,'two',{amount:'0',evidence:'本季未确收'}),r)[0].entries[0].amount,'0');
  assert.equal(form.totalText([{eligible:true,amount:'0'},{eligible:true,amount:''}]),'0 万元 · 已填小计，另 1 项未登记');
  assert.equal(form.sumAmounts(['0.01','10000000000000000.99']),'10000000000000001');
});

test('依据和备注也可直接更正；缺依据、清空金额、超精度分别提示，不把未知补零',()=>{
  const r=response({items:[item('one','100000.00')]});
  assert.equal(send(edited(r,'one',{evidence:'新凭证'}),r)[0].entries[0].source_ref,'新凭证');
  for (const fields of [{amount:''},{evidence:''},{amount:'0.0000001'}]) assert.throws(()=>send(edited(r,'one',fields),r));
  assert.equal(form.totalText([{eligible:true,amount:''}]),'未登记');
});

test('后台can_edit与每项eligible双重限制；旧来源需要确认，不自动拆历史总额',()=>{
  const denied=response({can_edit:false});
  assert.throws(()=>send(edited(denied,'one',{amount:'1',evidence:'依据'}),denied),/无权修改/);
  const r=response({items:[item('one',null,{eligible:false})]});
  assert.throws(()=>send(edited(r,'one',{amount:'1',evidence:'依据'}),r),/不允许/);
  const legacy=response({actual:{...response().actual,amount:'300000.00',known_subtotal:'300000.00',source:'historical',requires_source_replacement:true,attribution_status:'unresolved'}});
  assert.equal(form.draftFor(legacy).rows.one.amount,'');
  const d=edited(legacy,'one',{amount:'15',evidence:'依据'});
  assert.throws(()=>send(d,legacy),/其他确收来源/);d.replaceSource=true;
  assert.equal(send(d,legacy)[0].replace_source,true);
  assert.deepEqual(send(form.draftFor({...legacy,items:[]}),{...legacy,items:[]}),[]);
});

function harness(get = async (_, requested) => response({period:requested})) {
  const session={workspaceId:'company',userId:'sales',permissionVersion:1};
  const c={properties:{existing:{id:'opportunity'},visitContext:false,disabled:false},sourceKey:'source',
    data:{...structuredClone(form.data),form:{materials_dirty:false},quarterOptions:[period,{year:2027,quarter:1,label:'2027 Q1'}]},emit(){},
    setData(changes){for(const [path,value] of Object.entries(changes)){const parts=path.split('.');let target=this.data;for(const key of parts.slice(0,-1))target=target[key]||(target[key]={});target[parts.at(-1)]=value;}},
    ...form.create({getOpportunityRecognizedActuals:get},()=>session)};
  const input=(id,key,value)=>c.recognizedActualInput({currentTarget:{dataset:{id,key}},detail:{value}});
  return {c,session,input};
}

test('跨季度草稿保留，刷新版本变化不静默覆盖他人实绩，显式核对后才提交',async()=>{
  let rev='a'.repeat(64),amount=null;
  const h=harness(async(_,requested)=>response({period:requested,actual:{...response().actual,revision:rev},items:[item('one',amount),item('two',amount)]}));
  await h.c.resetRecognizedActuals();h.input('one','amount','15');h.input('one','evidence','我的凭证');
  await h.c.recognizedActualQuarter({detail:{value:1}});assert.equal(h.c.data.recognizedActualDirty,false);
  await h.c.recognizedActualQuarter({detail:{value:0}});assert.equal(h.c.data.recognizedActualRows[0].amount,'15');
  rev='b'.repeat(64);amount='300000.00';await h.c.retryRecognizedActual();
  assert.equal(h.c.data.recognizedActualRows[0].amount,'15');
  assert.equal(h.c.data.recognizedActualRows[1].amount,'30');
  assert.equal(h.c.data.recognizedActualNeedsReview,true);assert.throws(()=>h.c.prepareRecognizedActuals(),/核对最新/);
  h.input(null,'reviewed',true);assert.equal(h.c.prepareRecognizedActuals()[0].entries.length,1);
  h.c.resetRecognizedActualDraft();assert.deepEqual(h.c.prepareRecognizedActuals(),[]);
});

test('读取失败不挡普通保存，但阻止未核实的实绩写入；草稿与登录身份隔离',async()=>{
  let fail=true;
  const h=harness(async()=>{if(fail)throw Error('读取失败');return response();});
  await h.c.resetRecognizedActuals();assert.deepEqual(h.c.prepareRecognizedActuals(),[]);
  fail=false;await h.c.retryRecognizedActual();h.input('one','amount','1');h.input('one','evidence','凭证');
  fail=true;await h.c.retryRecognizedActual();assert.throws(()=>h.c.prepareRecognizedActuals(),/读取失败/);
  assert.equal(h.c.data.form.recognized_actual_drafts['2026-Q4'].rows.one.amount,'1');
  let resolve;const late=harness(()=>new Promise(done=>{resolve=done;}));const promise=late.c.resetRecognizedActuals();
  late.session.userId='another';resolve(response());await promise;assert.equal(late.c.data.recognizedActualLoaded,false);
  assert.throws(()=>late.c.prepareRecognizedActuals(),/账号已变化/);
});

test('新增/跟进内嵌不请求确收，锁定表单不可写；错误映射不显示内部字段',async()=>{
  let calls=0;const h=harness(async()=>{calls++;return response();});h.c.properties.visitContext=true;
  await h.c.resetRecognizedActuals();assert.equal(calls,0);assert.deepEqual(h.c.prepareRecognizedActuals(),[]);
  h.c.properties.visitContext=false;h.c.properties.existing=null;await h.c.resetRecognizedActuals();assert.equal(calls,0);
  h.c.properties.existing={id:'opportunity'};await h.c.resetRecognizedActuals();h.c.properties.disabled=true;h.input('one','amount','1');assert.deepEqual(h.c.prepareRecognizedActuals(),[]);
  assert.match(errors.messageFor({detail:[{loc:['body','recognized_actuals',0,'entries',0,'source_ref'],type:'missing'}]},422),/确收依据/);
  assert.match(errors.messageFor({code:'OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT'},409),/刷新确收状态/);
});


test('不可编辑原因使用错误码中文映射，未知值和对象原型键不会泄露到页面',async()=>{
  for(const reason of ['OPPORTUNITY_TRADE_RELATION_REQUIRED','OPPORTUNITY_RECOGNIZED_MATERIAL_REQUIRED','OPPORTUNITY_RECOGNIZED_FORBIDDEN','SQL private_table denied','constructor']) {
    const h=harness(async()=>response({can_edit:false,editable_reason:reason}));await h.c.resetRecognizedActuals();
    assert.equal(h.c.data.recognizedActualReason,Object.prototype.hasOwnProperty.call(errors.MESSAGES,reason)?errors.MESSAGES[reason]:'当前实际确收暂不可编辑，请刷新状态后重试');
    assert.equal(typeof h.c.data.recognizedActualReason,'string');assert.doesNotMatch(h.c.data.recognizedActualReason,/OPPORTUNITY_|SQL|private_table|constructor/);
  }
});
