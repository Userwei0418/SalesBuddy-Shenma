require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const presentation = require('../miniprogram/utils/fdePresentation');
const {drawRadar} = require('../miniprogram/utils/fdeRadar');
const base = path.resolve(__dirname, '../miniprogram');
const session = {role: 'fde_lead', userId: 'u1', workspaceId: 'w1', permissionVersion: 'v1', capabilities: {'team.view': true}};
function component(relative, api = {}, extras = {}) {
  let definition;
  const app = {globalData: {session: {...session}}}, timers = [];
  const filename = path.join(base, relative);
  const context = {Component: value => definition = value, require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)), getApp: () => app, Date, console,
    setTimeout: callback => {timers.push(callback); return timers.length;}, clearTimeout() {},
    wx: {showToast() {}, ...extras}};
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context);
  return {app, timers, instance: {...definition, ...definition.methods, properties: {}, data: JSON.parse(JSON.stringify(definition.data)),
    setData(data, callback) {Object.assign(this.data, data); if (callback) callback();}, triggerEvent() {}}};
}
const codes = ['record_quality', 'plan_coverage', 'project_coverage', 'customer_coverage', 'task_completion', 'task_timeliness'];
function payload(overrides = {}) {
  return {data_source: 'database', sample_count: 0, as_of: '2026-09-13T17:30:00Z', review_status: 'empty', framework: {dimensions: codes.map(code => ({code, name: code, short_name: code}))}, latest: {overall_score: null, summary: '本人工作事实', dimensions: codes.map(code => ({code, score: null, evidence_count: 0})), advice: []}, ...overrides};
}

test('北京时间跨日显示和未知金额不会当成零', () => {
  assert.equal(presentation.beijingTime('2026-09-13T17:30:00Z'), '2026/09/14 01:30');
  assert.equal(presentation.beijingTime('2026-09-13T17:30:00+08:00'), '2026/09/13 17:30');
  assert.equal(presentation.beijingTime('invalid'), '');
  assert.equal(presentation.money(null), '未登记');
  assert.equal(presentation.money(0), '0');
});

test('阶段、节奏和排行图直接按真实值缩放，零不绘制假柱', () => {
  assert.deepEqual(presentation.stageBars([{count: 2, amount: 10000}, {count: 1, amount: 0}, {count: 0, amount: null}]).map(row => row.width), [100, 50, 0]);
  assert.deepEqual(presentation.rhythmBars([{date: '2026-09-01', visits: 4}, {date: '2026-09-03', visits: 1}]).map(row => [row.dateLabel, row.height]), [['09/01', 100], ['09/03', 25]]);
  assert.deepEqual(presentation.rankedRows([{name: '乙', visits: 0}, {name: '甲', visits: 2}], 'visits').map(row => [row.name, row.rank, row.width]), [['甲', 1, 100], ['乙', 2, 0]]);
});

test('六维雷达缺失维度留空；全部缺失不画数据面或原点，零分仍是真实点', () => {
  function context() {const calls = []; return {calls, ctx: new Proxy({}, {get: (_, method) => (...args) => calls.push([method, ...args])})};}
  const empty = context();
  drawRadar(empty.ctx, 320, 220, codes.map(name => ({name, score: null})));
  assert.equal(empty.calls.filter(call => call[0] === 'arc').length, 0);
  assert.equal(empty.calls.filter(call => call[0] === 'fill').length, 0);
  assert.equal(empty.calls.filter(call => call[0] === 'fillText' && call[1] === '未评估').length, 6);
  const partial = context();
  drawRadar(partial.ctx, 320, 220, codes.map((name, i) => ({name, score: i < 2 ? i * 50 : null})));
  assert.equal(partial.calls.filter(call => call[0] === 'arc').length, 2);
  assert.equal(partial.calls.filter(call => call[0] === 'fill').length, 2);
  const complete = context();
  drawRadar(complete.ctx, 320, 220, codes.map(name => ({name, score: 0})));
  assert.equal(complete.calls.filter(call => call[0] === 'arc').length, 6);
  assert.equal(complete.calls.filter(call => call[0] === 'fill').length, 7);
  for (const width of [260, 320]) {
    const full = context();
    drawRadar(full.ctx, width, 220, codes.map(name => ({name, score: 100})));
    const vertices = full.calls.filter(call => call[0] === 'arc');
    const labels = full.calls.filter(call => call[0] === 'fillText' && codes.includes(call[1]));
    for (const i of [1, 2]) assert.ok(labels[i][2] - vertices[i][1] >= 12, 'right labels clear the plot');
    for (const i of [4, 5]) assert.ok(vertices[i][1] - labels[i][2] >= 12, 'left labels clear the plot');
    assert.ok(labels[3][3] - 11 >= vertices[3][2] + 8, 'bottom label clears its vertex');
    assert.ok(labels[3][3] + 15 <= 220 - 6, 'bottom score remains inside the canvas');
  }
});

test('FDE我的首次读取不触发模型，无样本六维保留null且没有销售总分', async () => {
  let posts = 0;
  const {instance} = component('components/fde-profile/index.js', {getFdeProfile: async () => payload(), reviewFdeProfile: async () => posts++});
  await instance.load();
  assert.equal(instance.data.ready, true);
  assert.equal(instance.data.updatedText, '2026/09/14 01:30');
  assert.ok(instance.data.dimensions.every(row => row.score === null && row.scoreText === '未评估'));
  assert.equal(Object.hasOwn(instance.data, 'overallScore'), false);
  await instance.generateAdvice();
  assert.equal(posts, 0);
});

test('AI建议只在点击生成时POST，pending轮询只GET且有上限', async () => {
  let reads = 0, posts = 0;
  const {instance, timers} = component('components/fde-profile/index.js', {getFdeProfile: async () => {reads++; return payload({review_status: posts ? 'running' : 'missing'});}, reviewFdeProfile: async () => {posts++; return {status: 'queued'};}});
  await instance.load(); assert.equal(posts, 0);
  await instance.generateAdvice(); assert.equal(posts, 1); assert.equal(reads, 2);
  await instance.generateAdvice(); assert.equal(posts, 1);
  await timers.shift()(); assert.equal(posts, 1); assert.equal(reads, 3);
  instance.pollCount = 40; instance.schedulePoll(); assert.equal(instance.data.pollPaused, true);
});

test('画像与AI建议响应在身份变化或页面隐藏后不回填', async () => {
  let resolve;
  const {instance, app} = component('components/fde-profile/index.js', {getFdeProfile: () => new Promise(done => resolve = done)});
  const pending = instance.load(); app.globalData.session.permissionVersion = 'v2'; resolve(payload()); await pending;
  assert.equal(instance.data.ready, false); assert.equal(instance.data.dimensions.length, 0);
  const hidden = instance.load(); instance.pause(); resolve(payload()); await hidden;
  assert.equal(instance.data.ready, false);
});

test('我的页面隐藏再显示会重新GET，旧请求不覆盖新时间和建议且记录重新读取', async () => {
  const responses = [], records = []; let posts = 0;
  const {instance} = component('components/fde-profile/index.js', {
    getFdeProfile: () => new Promise(resolve => responses.push(resolve)),
    reviewFdeProfile: async () => posts++,
    getFdeActivity: async params => {records.push(params); return {items: [], total: 0, has_more: false, next_offset: null};},
  });
  instance.lifetimes.attached.call(instance);
  responses[0](payload({as_of: '2026-09-13T05:30:00Z'}));
  await new Promise(done => setImmediate(done));
  assert.equal(instance.data.updatedText, '2026/09/13 13:30');
  const stale = instance.load();
  instance.pageLifetimes.hide.call(instance);
  instance.pageLifetimes.show.call(instance);
  assert.equal(responses.length, 3);
  assert.equal(instance.data.ready, false);
  const current = payload({as_of: '2026-09-13T06:30:00Z', review_status: 'succeeded'});
  current.latest.advice = [{title: '当前安排', content: '核对已有计划中的材料'}];
  responses[2](current); await new Promise(done => setImmediate(done));
  responses[1](payload({as_of: '2026-09-13T05:30:00Z'})); await stale;
  assert.equal(instance.data.updatedText, '2026/09/13 14:30');
  assert.equal(instance.data.advice[0].title, '当前安排');
  instance.tab({currentTarget: {dataset: {tab: 'records'}}});
  await new Promise(done => setImmediate(done));
  instance.pageLifetimes.hide.call(instance);
  instance.pageLifetimes.show.call(instance);
  responses[3](current); await new Promise(done => setImmediate(done));
  assert.equal(records.length, 2);
  assert.ok(records.every(query => query.offset === 0 && query.scope === 'self'));
  assert.equal(posts, 0);
});

test('我的拜访固定本人历史，画像接口失败不妨碍记录，摘要不能跳全文', async () => {
  const queries = [], opened = [];
  const {instance} = component('components/fde-profile/index.js', {getFdeProfile: async () => {throw Error('offline');}, getFdeActivity: async params => {queries.push(params); return {items: [{id: 'v1', customer_id: 'c1', interaction_at: '2026-09-13T17:30:00Z', can_read_detail: false}], total: 1, has_more: false, next_offset: null};}}, {navigateTo: options => opened.push(options.url)});
  instance.setData({activeTab: 'records'}); await instance.load(); await new Promise(done => setImmediate(done));
  assert.equal(queries[0].scope, 'self'); assert.equal(queries[0].period, 'all'); assert.equal(instance.data.records.length, 1);
  instance.openVisit({currentTarget: {dataset: {id: 'v1'}}}); assert.equal(opened.length, 0);
  instance.data.records[0].can_read_detail = true; instance.openVisit({currentTarget: {dataset: {id: 'v1'}}}); assert.match(opened[0], /visit-detail/);
});

test('看板成员钻取固定team上下文，周期选择确定前不请求', async () => {
  const requests = [];
  const {instance} = component('components/fde-dashboard/index.js', {getFdeDashboard: async params => {requests.push(params); return {data_source: 'database', summary: {}, ranking: [], recent_visits: [], members: [{id: 'u2', name: '另一成员'}]};}});
  instance.properties = {memberId: 'u2'}; instance.setData({scope: 'self', year: 2026, yearOptions: [2025, 2026], yearIndex: 1});
  await instance.load(); assert.equal(requests[0].scope, 'team'); assert.equal(requests[0].member_id, 'u2');
  instance.scope({currentTarget: {dataset: {scope: 'self'}}}); assert.equal(requests.length, 1);
  instance.toggleFilter(); instance.quarter({currentTarget: {dataset: {q: 1}}}); instance.year({detail: {value: 0}}); assert.equal(requests.length, 1);
  instance.applyPeriod(); await new Promise(done => setImmediate(done)); assert.equal(requests.length, 2); assert.equal(requests[1].year, 2025); assert.deepEqual(Array.from(requests[1].quarters), [1]);
});

test('FDE新组件只使用微信允许的局部类选择器，我的不再嵌入统计板', () => {
  for (const component of ['fde-dashboard', 'fde-profile']) {
    const css = fs.readFileSync(path.join(base, 'components', component, 'index.wxss'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const match of css.matchAll(/([^{}]+)\{/g)) {
      for (const selector of match[1].trim().split(',')) {
        assert.doesNotMatch(selector, /(^|[\s>+~])(view|text|label|button|page|canvas)\b|#[\w-]+|\[[^\]]*\]/, selector);
      }
    }
  }
  const profile = fs.readFileSync(path.join(base, 'pages/profile/index.wxml'), 'utf8');
  assert.match(profile, /fde-profile/); assert.doesNotMatch(profile, /fde-dashboard/);
});

test('FDE我的所有可点击入口使用原生按钮，页签和历史记录有明确读屏名称', () => {
  const markup = fs.readFileSync(path.join(base, 'components/fde-profile/index.wxml'), 'utf8');
  const actions = [...markup.matchAll(/<(\w+)\b([^>]*\bbindtap="[^"]+"[^>]*)>/g)];
  assert.ok(actions.length >= 8);
  for (const [, tag] of actions) assert.equal(tag, 'button', 'interactive content must expose native button semantics');
  const tabs = actions.find(([, , attributes]) => attributes.includes('data-tab="{{item.key}}"'))[2];
  assert.match(tabs, /aria-label="[^\"]*item.label[^\"]*当前已选中/);
  assert.ok(!actions.some(([, , attributes]) => attributes.includes('data-tab="advice"')), 'duplicate advice entry has been removed');
  const record = actions.find(([, , attributes]) => attributes.includes('bindtap="openVisit"'))[2];
  assert.match(record, /aria-label="[^\"]*item.customer_name[^\"]*item.dateText/);
  assert.match(record, /查看拜访详情.*历史摘要，当前不可查看详情/);
});

test('请求新AI建议立即清除旧建议，失败或非成功响应不展示旧内容', async () => {
  let rejectPost;
  const response = payload({review_status: 'succeeded'});
  response.latest.advice = [{title: '原建议', content: '原内容'}];
  const {instance} = component('components/fde-profile/index.js', {getFdeProfile: async () => response, reviewFdeProfile: () => new Promise((_, reject) => rejectPost = reject)});
  await instance.load(); assert.equal(instance.data.advice.length, 1);
  const pending = instance.generateAdvice(); assert.equal(instance.data.advice.length, 0); assert.equal(instance.data.reviewStatus, 'queued');
  rejectPost(Error('network failed')); await pending; assert.equal(instance.data.advice.length, 0); assert.equal(instance.data.reviewStatus, 'failed');
  response.review_status = 'running'; await instance.load(true); assert.equal(instance.data.advice.length, 0);
});

test('本人看板标题不误用人员目录的全部成员默认项', async () => {
  const {instance} = component('components/fde-dashboard/index.js', {getFdeDashboard: async () => ({data_source: 'database', scope_label: '个人协作', summary: {}, ranking: [], recent_visits: [], members: []})});
  instance.setData({scope: 'self', year: 2026}); await instance.load(); assert.equal(instance.data.memberLabel, ''); assert.equal(instance.data.scopeLabel, '个人协作');
});

test('AI成功且无新增建议有明确空态，不自动重跑或伪造建议', async () => {
  let posts = 0;
  const {instance, timers} = component('components/fde-profile/index.js', {getFdeProfile: async () => payload({sample_count: 1, review_status: 'succeeded'}), reviewFdeProfile: async () => posts++});
  await instance.load();
  assert.equal(instance.data.reviewStatus, 'succeeded');
  assert.equal(instance.data.reviewBusy, false);
  assert.equal(instance.data.advice.length, 0);
  assert.equal(instance.data.reviewMessage, '已分析，当前没有需要额外补充的协作建议');
  assert.equal(posts, 0);
  assert.equal(timers.length, 0);
  await instance.load();
  assert.equal(posts, 0);
});


test('看板仅显示有商机的阶段，保持业务顺序和真实金额', () => {
  const present = require('../miniprogram/utils/fdePresentation');
  const rows = present.visibleStageBars([
    {code:'solution',count:2,amount:null},
    {code:'identified',count:0,amount:0},
    {code:'qualified',count:2,amount:500000},
    {code:'won',count:1,amount:0},
    {code:'lost',count:null,amount:null}
  ]);
  assert.deepEqual(rows.map(row=>row.code),['qualified','solution','won']);
  assert.equal(rows[0].amountText,'50');
  assert.equal(rows[1].amountText,'未登记');
  assert.equal(rows[2].amountText,'0');
  assert.deepEqual(present.visibleStageBars([]),[]);
  assert.deepEqual(present.visibleStageBars([{code:'identified',count:0,amount:0}]),[]);
});

test('FDE项目成效与协作效率读取本人看板，缺失指标不冒充零', async()=>{
  const calls=[];
  const {instance}=component('components/fde-profile/index.js',{
    getFdeDashboard:async params=>{calls.push(params);return {data_source:'database',summary:{opportunities:4,recognized_amount:12000,period_visits:6,completed_tasks:3},stages:[],rhythm:[]};}
  });
  await instance.loadMetrics();
  assert.equal(calls[0].scope,'self');
  assert.equal(instance.data.projectMetrics.find(x=>x.name==='参与商机').value,'4');
  assert.equal(instance.data.projectMetrics.find(x=>x.name==='确收金额').value,'1.2');
  assert.equal(instance.data.projectMetrics.find(x=>x.name==='回款金额').value,'未登记');
  assert.equal(instance.data.collaborationMetrics.find(x=>x.name==='归档跟进').value,'6');
  assert.equal(instance.data.metricsLoading,false);
});

test('FDE协作统计在账号变化后不回填',async()=>{
  let resolve;
  const {instance,app}=component('components/fde-profile/index.js',{getFdeDashboard:()=>new Promise(r=>resolve=r)});
  const pending=instance.loadMetrics();
  app.globalData.session={...session,userId:'another'};
  resolve({data_source:'database',summary:{opportunities:99}});
  await pending;
  assert.equal(instance.data.projectMetrics.length,0);
});

test('FDE主管选择器遮挡期间停止两张画布，关闭后仅重绘已有数据',()=>{
 const created=[],callbacks=[],{instance}=component('components/fde-profile/index.js',{}, {createCanvasContext:id=>{created.push(id);return new Proxy({}, {get:()=>()=>{}});}});
 instance.properties={chartsHidden:true};instance.setData({ready:true,activeTab:'profile',dimensions:codes.map(code=>({code,name:code,score:60})),history:[{date:'2026-09-16',dimensions:codes.map(code=>({code,score:60}))}]});
 instance.createSelectorQuery=()=>({select(){return this;},boundingClientRect(fn){callbacks.push(fn);return this;},exec(){}});
 instance.drawRadar();instance.selectHistory();assert.equal(callbacks.length,0);
 instance.properties.chartsHidden=false;instance.observers.chartsHidden.call(instance,false);
 assert.equal(callbacks.length,2);
 instance.properties.chartsHidden=true;callbacks.splice(0).forEach(fn=>fn({width:300,height:200}));assert.equal(created.length,0);
 instance.properties.chartsHidden=false;instance.observers.chartsHidden.call(instance,false);
 callbacks.splice(0).forEach(fn=>fn({width:300,height:200}));
 assert.deepEqual(created,['fdeAbilityRadar','fdeHistory']);
});
