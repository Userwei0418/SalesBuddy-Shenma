require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const opp=require('../miniprogram/utils/opportunity');
const row={id:'o',name:'原商机',amount:120000,sales_channel:'direct',status:'open',probability:10,summary:'说明',version_no:3};
test('打开未修改的历史缺字段商机不产生更新；单次参与人也不产生更新',()=>{
 const form=opp.formForVisit(row);assert.equal(opp.visitHasChanges(form,row),false);
 form.visit_fde_members=[{id:'u'}];form.visit_fde_member_ids=['u'];assert.equal(opp.visitHasChanges(form,row),false);
});
test('实际变更会提交，改回原值和等值金额不提交',()=>{
 const form=opp.formForVisit(row);form.name='新名称';assert.equal(opp.visitHasChanges(form,row),true);
 form.name=row.name;form.amount='12.000000';assert.equal(opp.visitHasChanges(form,row),false);
 form.amount='13';assert.equal(opp.visitHasChanges(form,row),true);
});
test('额外信息恢复原值不更新；长期成员变化仍更新',()=>{
 const form=opp.formForVisit(row);form.extra_dirty.summary=true;form.extra_values.summary='说明';assert.equal(opp.visitHasChanges(form,row),false);
 form.extra_values.summary='新说明';assert.equal(opp.visitHasChanges(form,row),true);
 form.extra_values.summary='说明';form.extra_dirty.fde_member_ids=true;form.extra_roster=[{id:'long-term'}];assert.equal(opp.visitHasChanges(form,row),true);
});
test('新增商机不是无变更；实际季度值、渠道与产品线变更均保留',()=>{
 assert.equal(opp.visitHasChanges(opp.formForVisit(null),null),true);
 for(const change of [f=>f.quarters=[{year:2026,quarter:4,recognized:'0',collection:'1'}],f=>{f.partner_mode='partner';f.partner_id='p';},f=>{f.intent_product_lines_dirty=true;f.intent_product_line_ids=['product'];}]){
  const form=opp.formForVisit(row);change(form);assert.equal(opp.visitHasChanges(form,row),true);
 }
});

test('真实表单prepare在历史必填校验及名称请求之前返回无修改',async()=>{
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');let definition;
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:d=>definition=d,require:n=>require(path.resolve(path.dirname(file),n)),getApp:()=>({globalData:{session:{}}}),wx:{}});
 const component={properties:{visitContext:true,existing:row},data:{form:opp.formForVisit(row)},
  validateExtras(){throw Error('不应验证未修改的历史字段');},checkName(){throw Error('不应发起名称请求');}};
 assert.equal(await definition.methods.prepare.call(component),null);
});
