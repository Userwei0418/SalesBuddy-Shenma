const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root=path.resolve(__dirname,'../miniprogram');
function page(wx={}) {
  let definition;
  vm.runInNewContext(fs.readFileSync(path.join(root,'pages/opportunity-collaboration/index.js'),'utf8'), {
    Page:value=>definition=value, wx,
    require:()=>{throw Error('Retired page must not load API or recovery dependencies');},
  });
  return definition;
}
test('旧协作链接仅显示取消说明，不读取候选、重试旧保存或清理存储',async()=>{
  const pending={key:'old-request',body:{version_no:7,member_ids:['historic-member']}};
  const store=new Map([['opportunityCollaboration:v1:old-scope',pending]]);
  const forbidden=()=>{throw Error('Retired page must not access storage or the network');};
  const p=page({getStorageSync:forbidden,setStorageSync:forbidden,removeStorageSync:forbidden,request:forbidden});
  for(const lifecycle of ['onLoad','onShow','onHide','onShow','onUnload'])await p[lifecycle]?.({id:'old-opportunity'});
  for(const action of ['load','reload','search','confirm','send'])assert.equal(typeof p[action],'undefined');
  assert.equal(store.get('opportunityCollaboration:v1:old-scope'),pending);
  assert.ok(JSON.parse(fs.readFileSync(path.join(root,'app.json'),'utf8')).pages.includes('pages/opportunity-collaboration/index'));
  const template=fs.readFileSync(path.join(root,'pages/opportunity-collaboration/index.wxml'),'utf8');
  assert.match(template,/商机协作设置已取消/);assert.match(template,/bindtap="goBack"/);
  assert.doesNotMatch(template,/can_manage_collaborators|bindtap="(?:confirm|search|reload)"|重试本次保存/);
});
test('退役说明安全返回上一页，直接打开时返回商机页；410使用友好提示',()=>{
  let back,tab;
  const p=page({navigateBack:options=>{back=options;},switchTab:options=>{tab=options.url;}});
  p.goBack();assert.equal(back.delta,1);assert.equal(tab,undefined);
  back.fail();assert.equal(tab,'/pages/workbench/index');
  const messages=require('../miniprogram/utils/errorMessages');
  assert.equal(messages.messageFor({code:'OPPORTUNITY_COLLABORATION_RETIRED'},410),'商机协作设置已取消，请返回商机继续操作');
});
