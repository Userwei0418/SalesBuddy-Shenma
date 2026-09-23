const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('编辑客户不能输入名称，提交也剔除被意外改变的名称，保留其他字段',async()=>{
 const filename=path.resolve(__dirname,'../miniprogram/pages/customer-edit/index.js');
 let page,submitted;
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
  Page:value=>page=value,
  require:name=>name.endsWith('/apiClient')?{updateCustomer:async(id,data)=>{submitted={id,data};}}:require(path.resolve(path.dirname(filename),name)),
  wx:{showToast(){},setStorageSync(){},navigateBack(){}},setTimeout(){},
 });
 page.data=JSON.parse(JSON.stringify(page.data));
 page.setData=values=>{for(const [key,value] of Object.entries(values)){if(key.startsWith('form.'))page.data.form[key.slice(5)]=value;else page.data[key]=value;}};
 page.data.customerId='customer-1';
 page.data.form={name:'原客户',industry:'企业软件',customer_type:'潜在客户',level_code:'Tier-1',source:'合作伙伴',partner_name:'伙伴',demand_summary:'原需求',next_action:'安排沟通',contact_name:'联系人',contact_title:'经理',contact_role:'使用者'};
 page.inputField({currentTarget:{dataset:{key:'name'}},detail:{value:'错误名称'}});
 assert.equal(page.data.form.name,'原客户');
 page.inputField({currentTarget:{dataset:{key:'partner_name'}},detail:{value:' 新伙伴 '}});
 page.data.form.name='意外更改';
 page.submit();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(submitted.id,'customer-1');assert.equal('name' in submitted.data,false);
 assert.equal(submitted.data.partner_name,'新伙伴');assert.equal('demand_summary' in submitted.data,false);assert.equal('next_action' in submitted.data,false);assert.equal(submitted.data.industry,'企业软件');assert.equal(submitted.data.contact_name,'联系人');
 const wxml=fs.readFileSync(filename.replace('.js','.wxml'),'utf8');
 assert.doesNotMatch(wxml,/form\.(demand_summary|next_action)/);assert.doesNotMatch(wxml,/<input[^>]*form\.name/);assert.match(wxml,/客户名称[\s\S]*不可修改/);
});
