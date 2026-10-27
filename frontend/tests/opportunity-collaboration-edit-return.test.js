const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const access=require('../miniprogram/utils/access');
test('商机编辑与转交不再提供旧协作入口，普通编辑返回保留草稿与保存恢复',async()=>{
  const root=path.resolve(__dirname,'../miniprogram'),session={workspaceId:'workspace',userId:'owner',role:'sales'};
  let definition,restores=0,loads=0;
  const api={getPendingOpportunitySave:()=>{restores++;return null;},getOpportunityAccess:()=>{throw Error('Retired collaboration read');}};
  const file=path.join(root,'pages/opportunity-create/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,setTimeout,clearTimeout,
    getApp:()=>({globalData:{session},guardPage:()=>true}),require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),name))});
  const p={...definition,data:{...definition.data,customerId:'customer',existing:{id:'opportunity',version_no:7}},
    setData:patch=>Object.assign(p.data,patch),opportunityId:'opportunity',loadedIdentity:access.identity(session),
    draftFields:{name:'未保存名称'},load:()=>{loads++;}};
  p.onHide();await p.onShow();
  assert.equal(p.draftFields.name,'未保存名称');assert.equal(p.data.existing.version_no,7);assert.equal(loads,0);assert.equal(restores,1);
  assert.equal(typeof p.submit,'function');assert.equal(typeof p.openCollaboration,'undefined');
  for(const name of ['opportunity-create','opportunity-transfer']){
    const source=fs.readFileSync(path.join(root,'pages',name,'index.js'),'utf8');
    const template=fs.readFileSync(path.join(root,'pages',name,'index.wxml'),'utf8');
    assert.doesNotMatch(source,/openCollaboration|opportunity-collaboration/);assert.doesNotMatch(template,/openCollaboration|商机协作/);
  }
  assert.match(fs.readFileSync(path.join(root,'pages/opportunity-transfer/index.js'),'utf8'),/api\.transferOpportunity/);
});
