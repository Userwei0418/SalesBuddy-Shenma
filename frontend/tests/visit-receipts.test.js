const test = require('node:test');
const assert = require('node:assert/strict');
const { mergeVisitReceipts } = require('../miniprogram/utils/visitCards');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flow = require('../miniprogram/utils/visitFlow');
const values = {follow_up_record:'沟通结果',next_action:'9月11日销售发送方案',interaction_at:'2026-09-10',created_date:'2026-09-10',contact_name:'客户经理'};
const quality = {follow_up_score:90,next_action:{passed:true}};
const source = {runId:'real-run',result:{fields:values,quality_review:quality}};

test('旧版草稿保留核对内容，但合并式评分不能替代独立质检',()=>{
  const oldDraft = {values, quality, customerConfirmed:true, customerId:'c', reviewRunId:'', reviewStale:true};
  const restored = flow.restoreReview(source,oldDraft);
  assert.deepEqual(restored.values,values);
  assert.equal(restored.customerId,'c');
  assert.equal(restored.reviewRunId,'');
  assert.equal(restored.quality,null);
  assert.equal(restored.flowStep,'edit');
  assert.match(flow.archiveBlockReason({...restored,reviewStale:false}), /先完成 AI 质量审核/);
  const edited = flow.restoreReview(source,{...oldDraft,values:{...values,follow_up_record:'新的沟通结果'}});
  assert.equal(edited.values.follow_up_record,'新的沟通结果');
  assert.equal(edited.reviewRunId,'');
});
test('新分阶段草稿保留独立质检回执，不回退到结构化run',()=>{
  const draft={flowVersion:1,values,quality,customerConfirmed:true,customerId:'c',
    reviewPayload:{fields:values,source_run_id:'structure-run'},reviewRunId:'quality-run',
    reviewedContent:'immutable-snapshot',reviewStale:false,flowStep:'result'};
  const restored=flow.restoreReview(source,draft);
  assert.equal(restored.reviewRunId,'quality-run');
  assert.equal(restored.reviewedContent,'immutable-snapshot');
  assert.equal(restored.quality.follow_up_score,90);
  assert.equal(flow.archiveBlockReason(restored),'');
});
test('待确认客户、修改正文、低分和下一步不通过，均给出原因',()=>{
  const d={values,quality,customerConfirmed:true,customerId:'c',reviewRunId:'r',reviewStale:false};
  assert.match(flow.archiveBlockReason({...d,customerConfirmed:false}),/确认.*客户/);
  assert.match(flow.archiveBlockReason({...d,reviewStale:true}),/正文已修改/);
  assert.match(flow.archiveBlockReason({...d,quality:{...quality,follow_up_score:60}}),/高于 60/);
  assert.match(flow.archiveBlockReason({...d,quality:{...quality,next_action:{passed:false}}}),/下一步审核/);
});
test('归档卡片重进恢复、刷新去重，取真实分数与可选商机，不伪造缺失评分',()=>{
  const greeting={id:'hi',kind:'greeting'}, task={id:'task',kind:'data-card'};
  const r={id:'v',customer_id:'c',customer_name:'客户',score:90,grade:'优秀',completed_count:8,total_count:12,next_action:'发送方案',archived_at:'2026-09-09T18:00:00Z',interaction_at:'2026-09-09T16:00:00Z'};
  const first=mergeVisitReceipts([greeting,task],[r,r]);
  assert.equal(first.length,3);
  assert.equal(first[1].time,'9月10日 02:00');
  assert.equal(first[1].card.subtitle,'客户 · 2026年9月10日');
  assert.equal(first[1].card.metrics[1].value,'90分');
  assert.equal(first[1].card.rows.length,1);
  assert.deepEqual(mergeVisitReceipts(first,[r]),first);
  assert.deepEqual(mergeVisitReceipts([greeting,task],[r]),first);
  assert.equal(mergeVisitReceipts(first,[]).length,2);
  const changed=mergeVisitReceipts(first,[{...r,opportunity_name:'商机',score:null}]);
  assert.equal(changed[1].card.rows.length,2);
  assert.equal(changed[1].card.metrics[1].value,'—');
});

test('归档回执仅在服务端明确允许时打开客户档案，旧接口安全打开跟进', () => {
  const build = require('../miniprogram/utils/visitCards').buildVisitReceipt;
  const record = { id: 'visit', customer_id: 'customer' };
  assert.deepEqual(build({ ...record, can_read_customer: true }).card.action,
    { label: '查看客户档案', code: 'open_archived_customer', customerId: 'customer', visitId: 'visit' });
  for (const can_read_customer of [false, undefined]) {
    assert.deepEqual(build({ ...record, can_read_customer }).card.action,
      { label: '查看跟进记录', code: 'open_archived_visit', visitId: 'visit' });
  }
});

function receiptPage(getCustomerHeader) {
  const file = path.resolve(__dirname, '../miniprogram/pages/index/index.js');
  const app = { globalData: { role: 'sales', session: { workspaceId: 'w1', userId: 'u1' } } };
  const storage = new Map([['pendingOpenOpportunityId', 'old-opportunity'], ['pendingBattleCustomerId', 'old-customer']]);
  const navigations = [], toasts = [];
  let page;
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value; }, getApp: () => app,
    require: name => name.endsWith('apiClient') ? { getCustomerHeader } : require(path.resolve(path.dirname(file), name)),
    clearInterval, clearTimeout,
    wx: { navigateTo: value => navigations.push(value.url), switchTab: value => navigations.push(value.url),
      showToast: value => toasts.push(value.title), setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key) },
  });
  page.data = { messages: [{ id: 'receipt' }] };
  page.setData = value => Object.assign(page.data, value);
  const click = action => page.handleCardAction({ currentTarget: { dataset: {
    action, visitId: 'visit', customerId: 'customer',
  } } });
  return { page, app, storage, navigations, toasts, click };
}

test('回执保留有权档案入口，仅403/404回退本次跟进，网络与服务错误保留卡片', async () => {
  let calls = 0;
  const allowed = receiptPage(async () => { calls++; return { id: 'customer' }; });
  await allowed.click('open_archived_visit');
  assert.equal(calls, 0);
  assert.deepEqual(allowed.navigations, ['/pages/visit-detail/index?visit_id=visit']);
  await allowed.click('open_archived_customer');
  assert.equal(calls, 1);
  assert.equal(allowed.navigations[1], '/pages/customers/index');
  assert.equal(allowed.storage.get('pendingOpenCustomerId'), 'customer');
  assert.equal(allowed.storage.has('pendingOpenOpportunityId'), false);
  assert.equal(allowed.storage.has('pendingBattleCustomerId'), false);
  for (const statusCode of [403, 404, 500, undefined]) {
    const denied = receiptPage(async () => { throw Object.assign(new Error('暂不可用'), { statusCode }); });
    const messages = denied.page.data.messages;
    await denied.click('open_archived_customer');
    assert.equal(denied.page.data.messages, messages);
    assert.equal(denied.storage.has('pendingOpenCustomerId'), false);
    assert.deepEqual(denied.navigations, [403, 404].includes(statusCode) ? ['/pages/visit-detail/index?visit_id=visit'] : []);
    assert.deepEqual(denied.toasts, [403, 404].includes(statusCode) ? [] : ['暂不可用']);
  }
});

test('撤离首页或切换公司后，旧档案预检回包不能触发跳转', async () => {
  for (const change of ['hide', 'workspace']) {
    let resolve;
    const result = receiptPage(() => new Promise(done => { resolve = done; }));
    const pending = result.click('open_archived_customer');
    if (change === 'hide') { result.page.onHide(); result.page.homeVisible = true; }
    else result.app.globalData.session.workspaceId = 'w2';
    resolve({ id: 'customer' });
    await pending;
    assert.deepEqual(result.navigations, []);
    assert.equal(result.storage.has('pendingOpenCustomerId'), false);
  }
});
