const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const contract=require('../miniprogram/utils/customerEditContract');
const base=()=>({industry:'企业软件',customer_type:'存量客户',level_code:'Tier-1',source:'公司分配',partner_name:'无',contact_name:'',contact_title:'',contact_role:'使用者'});

test('编辑旧客户未填联系人时省略空字段和默认角色，通用历史类型与来源不转换',()=>{
 const form=base(),result=contract.payload(form,{...form});
 assert.equal('customer_type' in result,false);assert.equal(result.source,'公司分配');
 for(const key of ['contact_name','contact_title','contact_role'])assert.equal(key in result,false);
});
test('未修改的联系人字段和未知历史角色省略，修改职位才发送职位',()=>{
 const original={...base(),contact_name:'原联系人',contact_role:'历史角色'};
 const result=contract.payload({...original,contact_title:'经理'},original);
 assert.equal(result.contact_title,'经理');assert.equal('contact_name' in result,false);assert.equal('contact_role' in result,false);
});
test('明确清空已有姓名或职位须提示用户，不能用省略或null悄悄忽略意图',()=>{
 const original={...base(),contact_name:'原联系人',contact_title:'经理'};
 assert.throws(()=>contract.payload({...original,contact_name:'  '},original),/联系人姓名.*不支持清空/);
 assert.throws(()=>contract.payload({...original,contact_title:''},original),/联系人职位.*不支持清空/);
 assert.equal(original.contact_name,'原联系人');
});
test('新增首要联系人提交姓名和页面角色，孤立职位或角色须补姓名',()=>{
 const original=base();
 const result=contract.payload({...original,contact_name:' 新联系人 ',contact_title:' 总监 '},original);
 assert.equal(result.contact_name,'新联系人');assert.equal(result.contact_title,'总监');assert.equal(result.contact_role,'使用者');
 assert.throws(()=>contract.payload({...original,contact_title:'总监'},original),/填写姓名/);
 assert.throws(()=>contract.payload({...original,contact_role:'决策者'},original),/填写姓名/);
});
test('联系人100字边界与接口一致，超限保留原输入而不截断',()=>{
 const original=base(),form={...original,contact_name:'字'.repeat(101)};
 assert.throws(()=>contract.payload(form,original),/联系人姓名最多填写 100 字/);
 assert.equal(form.contact_name.length,101);
 assert.equal([...contract.payload({...original,contact_name:'😀'.repeat(100)},original).contact_name].length,100);
 assert.throws(()=>contract.payload({...original,contact_name:'名字',contact_title:'职'.repeat(101)},original),/联系人职位最多填写 100 字/);
});
test('编辑不提交只读字段，拒绝新选择的无效角色和优先级',()=>{
 const form={...base(),name:'错误改名',demand_summary:'原需求',next_action:'原下一步',extra:'外来字段'};
 const result=contract.payload(form,base());
 for(const key of ['name','demand_summary','next_action','extra'])assert.equal(key in result,false);
 assert.throws(()=>contract.payload({...base(),level_code:'Tier-4'},base()),/优先级/);
 assert.throws(()=>contract.payload({...base(),contact_name:'名字',contact_role:'采购员'},base()),/联系人角色/);
});
test('422字段反馈只消费固定白名单，不展示后端输入、上下文或陌生字段',()=>{
 const result=contract.errorMessage({message:'原错误',validationErrors:[
  {loc:['body','contact_title'],type:'string_too_long',input:'不应展示',msg:'不应展示'},
  {loc:['body','contact_title'],type:'string_too_short'},
  {loc:['body','__proto__'],type:'missing'},
  {loc:['query','contact_name'],type:'missing'},
  {loc:['body','secret'],type:'missing'}]});
 assert.equal(result,'请检查联系人职位的填写内容');
});

function harness(updateCustomer=async()=>({id:'c'})){
 const filename=path.resolve(__dirname,'../miniprogram/pages/customer-edit/index.js');
 const app={globalData:{session:{userId:'u',workspaceId:'w'}}};let page;const calls=[],toasts=[];
 const api={getBusinessOptions:async()=>({}),getCustomerOverview:async()=>({id:'c',name:'原客户',industry_code:'企业软件',customer_type_code:'存量客户',source_code:'公司分配',level_code:'Tier-1'}),updateCustomer:async(id,data)=>{calls.push({id,data});return updateCustomer(id,data);}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,
  require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),name)),
  wx:{showToast:v=>toasts.push(v.title),setStorageSync(){},navigateBack(){}},setTimeout(){},clearTimeout(){}});
 page.data=JSON.parse(JSON.stringify(page.data));page.data.customerId='c';
 page.setData=values=>{for(const [key,value] of Object.entries(values)){if(key.startsWith('form.'))page.data.form[key.slice(5)]=value;else page.data[key]=value;}};
 return{page,app,calls,toasts};
}
test('实际页面加载无首要联系人后修改其他字段可保存，未误发空姓名/职位',async()=>{
 const h=harness();await h.page.loadCustomer();h.page.inputField({currentTarget:{dataset:{key:'partner_name'}},detail:{value:'新伙伴'}});
 await h.page.submit();assert.equal(h.calls.length,1);assert.equal(h.calls[0].data.partner_name,'新伙伴');
 for(const key of ['contact_name','contact_title','contact_role'])assert.equal(key in h.calls[0].data,false);
 assert.equal(h.page.data.saving,false);
});
test('实际页面422保留输入和字段提示，解除保存锁；身份变化的迟到错误不展示',async()=>{
 const error=Object.assign(Error('字段不符合要求'),{statusCode:422,validationErrors:[{loc:['body','contact_title'],type:'string_too_long'}]});
 const h=harness(async()=>{throw error;});await h.page.loadCustomer();h.page.data.form.contact_name='新联系人';h.page.data.form.contact_title='经理';
 await h.page.submit();assert.equal(h.calls.length,1);assert.equal(h.page.data.form.contact_title,'经理');assert.match(h.page.data.submissionError,/联系人职位/);assert.equal(h.page.data.saving,false);
 let reject;const late=harness(()=>new Promise((_,r)=>reject=r));await late.page.loadCustomer();const request=late.page.submit();
 late.app.globalData.session.userId='other';reject(error);await request;assert.equal(late.page.data.submissionError,'');assert.equal(late.toasts.length,0);
});
