const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../miniprogram/pages/demo-create/index.js'),'utf8');

function setup({canEdit=true,role='fde',headerCustomer='customer',sceneOpportunity='opportunity',createAllowed=true}={}){
  const session={workspaceId:'workspace',userId:'colleague',role};
  const calls={read:[],updated:[],created:[],deleted:[],modals:[]};
  const saved={id:'scene/id',opportunity_id:sceneOpportunity,name:'知识库问答',description:'演示内容',
    creator_name:'登记同事',created_at:'2026-09-14T00:00:00Z',version_no:3,can_edit:canEdit};
  const api={
    // Header is a customer envelope, not an opportunity row.
    getOpportunityDetailHeader:async id=>{calls.read.push(id);return {id:headerCustomer,name:'测试客户',
      opportunities:[{id:'opportunity',customer_id:headerCustomer,name:'测试商机'}],read_model:'detail_header_v1'};},
    getDemoScene:async()=>saved,
    listDemoScenes:async()=>({items:[],editable:createAllowed,total:0,data_source:'database'}),
    updateDemoScene:async(...args)=>calls.updated.push(args),
    createDemoScenes:async(...args)=>calls.created.push(args),
    deleteDemoScene:async(...args)=>calls.deleted.push(args),
  };
  let page;
  vm.runInNewContext(source,{
    Page:p=>page=p,getApp:()=>({ensureLogin:()=>true,globalData:{session}}),
    wx:{setNavigationBarTitle(){},showToast(){},navigateBack(){},showModal:o=>calls.modals.push(o)},
    require:name=>name.endsWith('apiClient')?api:name.endsWith('access')
      ? {identity:s=>s.workspaceId+':'+s.userId,can:()=>false}
      : {beijingTime:value=>'北京时间 '+value},
  });
  page.data=JSON.parse(JSON.stringify(page.data));
  page.setData=values=>Object.assign(page.data,values);
  page.getOpenerEventChannel=()=>({emit(){}});
  const link={customer_id:'customer',opportunity_id:'opportunity',demo_id:encodeURIComponent(saved.id),view:'1'};
  return {page,calls,link,saved};
}

test('真实header客户包正确拆出商机，编码场景链接可查看及编辑',async()=>{
  const {page,calls,link,saved}=setup();
  await page.onLoad(link);
  assert.equal(page.data.eligible,true);assert.equal(page.data.viewing,true);
  assert.equal(page.data.opportunity.id,'opportunity');assert.equal(page.editId,saved.id);
  assert.equal(page.data.sceneDetail.name,saved.name);
  page.startEditing();assert.equal(page.data.viewing,false);
  await page.submitDemoScenes();assert.equal(calls.updated.length,1);
  assert.equal(calls.updated[0][0],saved.id);assert.equal(calls.updated[0][1].version_no,3);
});

test('只读详情不受角色或visit.create阻断，can_edit=false不能编辑删除提交',async()=>{
  for(const role of ['fde','sales']){
    const {page,calls,link}=setup({canEdit:false,role});
    // Even a manually crafted edit link resolves to read-only when the API denies editing.
    await page.onLoad({...link,view:'0'});
    assert.equal(page.data.eligible,true);assert.equal(page.data.viewing,true);
    page.startEditing();assert.equal(page.data.viewing,true);
    page.deleteScene();await page.submitDemoScenes();
    assert.equal(calls.modals.length,0);assert.equal(calls.updated.length,0);assert.equal(calls.deleted.length,0);
  }
});

test('服务端允许维护时，销售账号不依赖拜访开关即可编辑Demo',async()=>{
  const {page,calls,link}=setup({role:'sales'});
  await page.onLoad(link);page.startEditing();await page.submitDemoScenes();
  assert.equal(calls.updated.length,1);
});

test('客户包错配或场景属于其他商机时拒绝加载',async()=>{
  for(const options of [{headerCustomer:'other-customer'},{sceneOpportunity:'other-opportunity'}]){
    const {page,link}=setup(options);await page.onLoad(link);
    assert.equal(page.data.eligible,false);assert.match(page.data.accessMessage,/不属于/);
  }
});

test('创建按Demo目录的editable授权，使用批量scenes请求',async()=>{
  const {page,calls}=setup();await page.onLoad({customer_id:'customer',opportunity_id:'opportunity'});
  assert.equal(page.data.eligible,true);
  page.setData({demoScenes:[{id:1,name:'场景一',description:'需求说明'}]});
  await page.submitDemoScenes();assert.equal(calls.created.length,1);
  assert.equal(calls.created[0][0],'opportunity');assert.equal(calls.created[0][1][0].name,'场景一');
  const denied=setup({createAllowed:false});
  await denied.page.onLoad({customer_id:'customer',opportunity_id:'opportunity'});
  assert.equal(denied.page.data.eligible,false);assert.match(denied.page.data.accessMessage,/不可创建/);
  await denied.page.submitDemoScenes();assert.equal(denied.calls.created.length,0);
});
