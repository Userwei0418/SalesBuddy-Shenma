const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const response={items:[{id:'p1',name:'授权项目',customer_name:'客户',amount:10000,status:'open',probability:10,fde_members:[]}],summary:{total:1,open_amount:10000},facets:{product_lines:[]},has_more:false,next_offset:null};

// Run the working-tree app capability refresh and actual page/component lifecycle;
// only the remote API and native host are replaced by deterministic test doubles.
function setup(){
  let app,appDefinition,pageDefinition,componentDefinition;
  const pages=[],requests=[];
  const api={getCurrentActor:async()=>({actor:{user_id:'fde-user',role:'fde',permission_version:'v2',capabilities:{'team.view':false,'customer.read':true},team_ids:[]}}),getAuth:()=>null,
    listOpportunities:params=>{const request={...deferred(),params};requests.push(request);return request.promise;}};
  const wx={setStorageSync(){},showToast(){},navigateTo(){}};
  function evaluate(file,globals){const filename=path.resolve(__dirname,'../miniprogram',file);vm.runInNewContext(fs.readFileSync(filename,'utf8'),{...globals,
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    getApp:()=>app,getCurrentPages:()=>pages,wx,Date,Set,Map,setTimeout,clearTimeout},{filename});}
  evaluate('app.js',{App:value=>{appDefinition=value;}});
  app={...appDefinition,globalData:{...appDefinition.globalData,role:'fde',session:{workspaceId:'workspace',userId:'fde-user',role:'fde',permissionVersion:'v1',loginAt:'first',capabilities:{'team.view':false,'customer.read':true},teamIds:[]}}};
  evaluate('pages/workbench/index.js',{Page:value=>{pageDefinition=value;}});
  evaluate('components/fde-projects/index.js',{Component:value=>{componentDefinition=value;}});
  const instance=definition=>({...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values,callback){Object.assign(this.data,values);if(callback)callback();}});
  const component={...instance(componentDefinition),...componentDefinition.methods,properties:{memberId:'',customerId:'',initialScope:''}};
  component.data.year=2026;
  const page=instance(pageDefinition);page._accessRoute='workbench';page.selectComponent=id=>id==='#fdeContent'?component:null;
  return {app,component,page,pages,requests,show:()=>component.pageLifetimes.show.call(component)};
}

for(const outcome of ['resolve','reject'])test(`隐藏页权限更新后旧请求${outcome}，返回必须启动新代际并解除loading`,async()=>{
  const h=setup(),old=h.component.load();
  h.show();assert.equal(h.requests.length,1,'当前代际首次请求尚未完成时show去重');
  let foregroundShows=0;
  h.pages.push(h.page,{data:{},onShow(){foregroundShows++;},setData(){},selectComponent(){return null;}});
  await h.app.refreshCapabilities(true);
  assert.equal(h.app.globalData.session.permissionVersion,'v2');assert.equal(foregroundShows,1);
  h.requests[0][outcome](outcome==='resolve'?response:Error('旧身份网络失败'));await old;
  assert.equal(h.component.data.loading,true,'迟到结果不写入当前身份');
  h.pages.pop();h.page.onShow();const latest=h.show();
  assert.equal(h.requests.length,2,'loading不能阻止新权限代际重载');
  h.show();assert.equal(h.requests.length,2);
  h.requests[1].resolve(response);await latest;await tick();
  assert.equal(h.component.data.loading,false);assert.equal(h.component.data.error,'');
  assert.equal(h.component.data.filtered[0].id,'p1');assert.equal(h.component.data.count,1);
});

test('返回后旧代际最后才完成也不能覆盖新项目或loading',async()=>{
  const h=setup(),old=h.component.load();
  h.pages.push(h.page,{data:{},onShow(){},setData(){},selectComponent(){return null;}});
  await h.app.refreshCapabilities(true);h.pages.pop();
  const latest=h.show();assert.equal(h.requests.length,2);
  h.requests[1].resolve({...response,items:[{...response.items[0],id:'new-project'}]});await latest;
  h.requests[0].resolve(response);await old;
  assert.equal(h.component.data.loading,false);assert.equal(h.component.data.filtered[0].id,'new-project');
});
