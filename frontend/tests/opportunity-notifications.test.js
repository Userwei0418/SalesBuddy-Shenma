const test=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
test('新商机变化卡片可见，轮询补充AI结果不重复发卡或移动位置',async()=>{
 let definition;const session={remote:true,workspaceId:'w',userId:'u'};
 const note={id:'notice',template_code:'business_changed',status:'pending',created_at:'2026-09-10T11:16:00Z',title:'商机有进展',payload:{tone:'green',event_id:'event',customer_id:'customer',opportunity_id:'opp',changes:[{label:'商机阶段',before:'商机确认－30%',after:'方案沟通－50%'}]}};
 const api={getTaskOverview:async()=>({items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}),isEnabled:()=>true,listNotifications:async()=>({items:[note]}),markNotificationRead:async()=>{note.status='read'}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/index/index.js','utf8'),{Page:p=>{definition=p},require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):n.includes('apiClient')?api:n.includes('taskOverview')?require('../miniprogram/utils/taskOverview'):{},getApp:()=>({globalData:{session}}),wx:{vibrateShort(){}},console});
 const p={...definition,homeVisible:true,data:{messages:[{id:'older',sortAt:new Date('2026-09-10T10:00:00Z').getTime()}]},setData(o,cb){Object.assign(this.data,o);if(cb)cb();}};
 p.consumeRemoteTaskNotification();await new Promise(setImmediate);
 assert.equal(p.data.messages.length,2);assert.equal(p.data.messages[0].id,'remote_notice');assert.equal(p.data.messages[1].id,'older');assert.equal(p.data.messages[0].time,'9月10日 19:16');assert.equal(p.data.messages[0].card.tone,'green');assert.match(p.data.messages[0].card.rows[0].meta,/30% → .*50%/);
 note.payload.ai_review={relationship_before:60,relationship_after:70,summary:'已提供试点样本'};
 p.consumeRemoteTaskNotification();await new Promise(setImmediate);
 assert.equal(p.data.messages.length,2);assert.equal(p.data.messages[0].card.rows.length,2);assert.equal(p.data.messages[1].id,'older');
});

test('商机动态卡片直接打开对应商机的只读详情',()=>{
 let definition;const routes=[];const storage=[];
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/index/index.js','utf8'),{Page:p=>{definition=p},require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):{},getApp:()=>({globalData:{session:{}}}),wx:{navigateTo:o=>routes.push(o.url),setStorageSync:(...args)=>storage.push(args),switchTab(){}},console});
 definition.handleCardAction({currentTarget:{dataset:{action:'open_changed_business',customerId:'customer 1',opportunityId:'opp/1'}}});
 assert.equal(routes.length,1);
 assert.match(routes[0],/customer-assets\/index\?customer_id=customer%201&opportunity_id=opp%2F1&period=all&readonly=1/);
 assert.equal(storage.length,0);
});

test('商机创建成功通知只展示阶段和金额摘要',()=>{
 let definition;
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/index/index.js','utf8'),{Page:p=>{definition=p},require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):{},getApp:()=>({globalData:{session:{}}}),wx:{},console});
 const message=definition.buildRemoteNotificationMessage({id:'n',template_code:'business_changed',title:'商机创建成功',body:'客户 · 新商机',payload:{tone:'blue',customer_id:'c',opportunity_id:'o',changes:[
  {label:'商机阶段',before:'新建',after:'商机确认－30%'},{label:'商机名称',before:'未填写',after:'新商机'},
  {label:'ACV（万元）',before:'未填写',after:'50'},{label:'预计关单日期',before:'未填写',after:'2026-09-13'},
  {label:'产品线',before:'未填写',after:'知识助手'}],ai_review:{relationship_before:0,relationship_after:20,summary:'建议'} }},{});
 assert.deepEqual(message.card.rows.map(row=>row.title),['商机阶段','ACV（万元）']);
 assert.equal(message.card.action.label,'查看商机');
});

test('客户关系按更新后的健康评分显示，不以是否变化决定颜色',()=>{
 let definition;
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/index/index.js','utf8'),{Page:p=>{definition=p},require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):{},getApp:()=>({globalData:{session:{}}}),wx:{},console});
 const improved=definition.buildRemoteNotificationMessage({id:'green',template_code:'business_changed',title:'客户信息已更新',body:'客户资料',payload:{tone:'blue',customer_id:'c',ai_review:{relationship_before:80,relationship_after:85,summary:'关系有进步'}}},{});
 assert.equal(improved.card.tone,'green');
 assert.equal(improved.card.statusLabel,'健康');
 const unchanged=definition.buildRemoteNotificationMessage({id:'blue',template_code:'business_changed',title:'客户信息已更新',body:'客户资料',payload:{tone:'blue',customer_id:'c',ai_review:{relationship_before:80,relationship_after:80,summary:'暂无变化'}}},{});
 assert.equal(unchanged.card.tone,'green');
 assert.equal(unchanged.card.statusLabel,'健康');
 const declined=definition.buildRemoteNotificationMessage({id:'red',template_code:'business_changed',title:'客户信息已更新',body:'客户资料',payload:{tone:'blue',customer_id:'c',ai_review:{relationship_before:85,relationship_after:80,summary:'关系有所下降'}}},{});
 assert.equal(declined.card.tone,'green');
 assert.equal(declined.card.statusLabel,'健康');
});
