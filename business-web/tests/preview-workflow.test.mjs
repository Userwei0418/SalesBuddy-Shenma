import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const mini = path.join(root, 'source/miniprogram');
const original = '沟通内容：已与客户核对试点边界，对方要求先提交验收清单。\n下一步计划：2026-09-18由我整理验收清单并发送给客户。\n跟进日期：2026-09-14\n对接人：示例联系人甲';
function workspace() {
  const window = {SALES_MODE: 'preview', setTimeout, clearTimeout, localStorage: {getItem() {return null;}, setItem() {}}};
  const context = vm.createContext({window, URL, URLSearchParams, setTimeout, clearTimeout, console});
  vm.runInContext(fs.readFileSync(path.join(root, 'preview-workflow.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'preview-api.js'), 'utf8'), context);
  const storage = new Map();
  context.wx = {request: options => window.SalesPreview.request(options), uploadFile: options => window.SalesPreview.uploadFile(options), getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key)};
  const cache = new Map();
  function load(filename) {
    const target = path.resolve(mini, filename.endsWith('.js') ? filename : filename + '.js');
    if (cache.has(target)) return cache.get(target).exports;
    const module = {exports: {}}; cache.set(target, module);
    vm.runInContext('(function(require,module,exports){' + fs.readFileSync(target, 'utf8') + '\n})', context)(specifier => load(path.relative(mini, path.resolve(path.dirname(target), specifier))), module, module.exports);
    return module.exports;
  }
  return {window, api: load('utils/apiClient'), flow: load('utils/visitFlow'), opportunity: load('utils/opportunity')};
}
async function login(w, role = 'sales') {await w.api.loginWithAccount('PREVIEW_' + role.toUpperCase(), 'example', role); return (await w.api.listCustomers()).items[0];}
async function review(w, customerId, fields, opportunityId) { return w.api.runAgent('visit_entry', w.flow.reviewText(fields), customerId, opportunityId); }

test('example extraction preserves explicit facts and never invents a next step or business date', () => {
  const w = workspace(), parser = w.window.SalesPreviewWorkflow;
  const fields = parser.parseVisit('客户只说需要进一步评估，没有确认预算。', '2026-09-14T03:00:00Z');
  assert.equal(fields.follow_up_record, '客户只说需要进一步评估，没有确认预算。');
  assert.equal(fields.next_action, ''); assert.equal(fields.interaction_at, ''); assert.equal(fields.contact_name, '');
  assert.equal(fields.created_date, '2026-09-14');
  const quality = parser.checkFields(fields);
  assert.equal(quality.follow_up_score, 25); assert.equal(quality.next_action.passed, false);
  assert.match(quality.suggestions[0], /不是销售质量评分/);
  const multiline = parser.parseVisit('沟通内容：客户提出两项要求；第一项先验证数据。\n第二项再评估部署。\n下一步计划：2026-09-18提交清单；同时整理验收口径。\n跟进日期：2026-09-14\n对接人：示例联系人甲', '2026-09-14T03:00:00Z');
  assert.equal(multiline.follow_up_record, '客户提出两项要求；第一项先验证数据。\n第二项再评估部署。');
  assert.equal(multiline.next_action, '2026-09-18提交清单；同时整理验收口径。');
});

test('source transcript, review, human archive and customer readback form a local example loop', async () => {
  const w = workspace(), customer = await login(w);
  const run = await w.api.runAgent('visit_entry', original, customer.id);
  const fields = run.result.fields, quality = run.result.quality_review;
  assert.equal(fields.contact_name, '示例联系人甲'); assert.equal(quality.review_kind, 'deterministic_field_check');
  assert.equal(quality.follow_up_score, 100); assert.equal(quality.next_action.passed, true);
  assert.equal(w.flow.archiveBlockReason({values: fields, customerId: customer.id, customerConfirmed: true, reviewRunId: run.id, quality, reviewStale: false}), '');
  const before = await w.api.listVisits({customer_id: customer.id});
  const saved = await w.api.createVisit(customer.id, {...fields, opportunity_id: null, _quality_review_run_id: run.id});
  assert.equal(saved.status, 'archived'); assert.equal(saved.source_label, '字段校验');
  const detail = await w.api.getVisit(saved.id);
  assert.equal(detail.follow_up_record, fields.follow_up_record); assert.equal(detail.next_action, fields.next_action);
  assert.equal(detail.visit_date, '2026-09-14'); assert.equal(detail.contact_name_snapshot, '示例联系人甲');
  assert.equal(typeof detail.within_seven_days, 'boolean');
  const after = await w.api.listVisits({customer_id: customer.id});
  assert.equal(after.total, before.total + 1); assert.equal(after.items[0].id, saved.id);
});

test('supplement updates the date and contact snapshots consumed by the original customer detail', async () => {
  const w = workspace(), customer = await login(w);
  const run = await w.api.runAgent('visit_entry', original, customer.id);
  const saved = await w.api.createVisit(customer.id, {...run.result.fields, _quality_review_run_id: run.id});
  const patched = await w.api.request({path: `/visits/${saved.id}`, method: 'PATCH', data: {version_no: saved.version_no, interaction_at: '2000-01-01', contact_name: '补充后的示例联系人', partner_name: '示例伙伴', collaborator_ids: [w.api.getAuth().actor.user_id]}});
  assert.equal(patched.visit_date, '2000-01-01'); assert.equal(patched.contact_name_snapshot, '补充后的示例联系人');
  assert.equal(patched.partner_name_snapshot, '示例伙伴'); assert.equal(patched.within_seven_days, false);
  assert.equal(patched.collaborators[0].name, w.api.getAuth().actor.display_name);
  assert.equal(patched.follow_up_record, saved.follow_up_record); assert.equal(patched.next_action, saved.next_action);
  const reread = await w.api.getVisit(saved.id); assert.equal(reread.contact_name_snapshot, patched.contact_name_snapshot);
});

test('missing action date, missing contact and edited stale review cannot be archived', async () => {
  const w = workspace(), customer = await login(w);
  const incomplete = await w.api.runAgent('visit_entry', original.replace('2026-09-18', ''), customer.id);
  assert.ok(incomplete.result.quality_review.follow_up_score < 100);
  await assert.rejects(w.api.createVisit(customer.id, {...incomplete.result.fields, _quality_review_run_id: incomplete.id}), /未通过/);
  const initial = await w.api.runAgent('visit_entry', original, customer.id);
  await assert.rejects(w.api.createVisit(customer.id, {...initial.result.fields, contact_name: '', _quality_review_run_id: initial.id}), /对接人/);
  const changed = {...initial.result.fields, follow_up_record: '客户更改了试点范围，需要重新核对。'};
  await assert.rejects(w.api.createVisit(customer.id, {...changed, _quality_review_run_id: initial.id}), /已修改/);
  const refreshed = await review(w, customer.id, changed);
  const saved = await w.api.createVisit(customer.id, {...changed, _quality_review_run_id: refreshed.id});
  assert.equal(saved.follow_up_record, changed.follow_up_record);
});

test('archive review is bound to actor/customer and first-visit fields are checked', async () => {
  const w = workspace(), customer = await login(w);
  const run = await w.api.runAgent('visit_entry', original, customer.id);
  const second = (await w.api.listCustomers()).items[1];
  await assert.rejects(w.api.createVisit(second.id, {...run.result.fields, _quality_review_run_id: run.id}), /关联对象已变化/);
  const first = await w.api.runAgent('visit_entry', '【录入类型：首次拜访】\n【拜访原始记录】\n' + original, customer.id);
  assert.equal(first.result.fields.is_first_visit, true);
  assert.equal(first.result.quality_review.check_count, 8);
  assert.equal(first.result.quality_review.follow_up_score, 50);
  await assert.rejects(w.api.createVisit(customer.id, {...first.result.fields, _quality_review_run_id: first.id}), /未通过/);
  const completed = {...first.result.fields, customer_main_business: '制造业检测设备', customer_needs: '核对脱敏测试样本', customer_budget: '本次尚未披露预算', contact_role: '项目对接人'};
  const rereview = await review(w, customer.id, completed);
  assert.equal(rereview.result.quality_review.follow_up_score, 100);
  const archived = await w.api.createVisit(customer.id, {...completed, _quality_review_run_id: rereview.id});
  assert.equal(archived.is_first_visit, true); assert.equal(archived.customer_budget, '本次尚未披露预算');
});

test('FDE must select its own participating opportunity and cannot mutate commercial fields', async () => {
  const w = workspace(), customer = await login(w, 'fde');
  await assert.rejects(w.api.runAgent('visit_entry', original, customer.id), /本人参与/);
  const opportunities = await w.api.listFdeVisitOpportunities({customer_id: customer.id});
  const opportunity = opportunities.items[0]; assert.ok(opportunity);
  const run = await w.api.runAgent('visit_entry', original, customer.id, opportunity.id);
  const fields = {...run.result.fields, opportunity_id: opportunity.id, _quality_review_run_id: run.id};
  await assert.rejects(w.api.createVisit(customer.id, {...fields, _opportunity_mutation: {action: 'create'}}), /不能修改商机/);
  const saved = await w.api.createVisit(customer.id, fields);
  assert.equal(saved.opportunity_id, opportunity.id);
  assert.ok(saved.fde_participant_ids.includes(w.api.getAuth().actor.user_id));
});

test('same archive idempotency key creates one visit; changed content requires a fresh review', async () => {
  const w = workspace(), customer = await login(w);
  const run = await w.api.runAgent('visit_entry', original, customer.id);
  const body = {customer_id: customer.id, fields: {...run.result.fields, _quality_review_run_id: run.id}};
  const auth = w.api.getAuth();
  const send = () => new Promise(resolve => w.window.SalesPreview.request({url: '/api/v1/visits', method: 'POST', data: body, header: {Authorization: 'Bearer ' + auth.access_token, 'Idempotency-Key': 'same-archive'}, success: resolve}));
  const first = await send(), second = await send();
  assert.equal(first.statusCode, 200); assert.equal(second.statusCode, 200); assert.equal(first.data.id, second.data.id);
  const rows = await w.api.listVisits({customer_id: customer.id});
  assert.equal(rows.items.filter(row => row.id === first.data.id).length, 1);
  await assert.rejects(w.api.createVisit(customer.id, body.fields), /已经归档/);
});

test('visit and commercial mutation validate together before creating or updating an opportunity', async () => {
  const w = workspace(), customer = await login(w);
  const beforeVisits = await w.api.listVisits({customer_id: customer.id});
  const beforeOps = await w.api.listCustomerOpportunities(customer.id);
  const run = await w.api.runAgent('visit_entry', original, customer.id);
  const form = {...w.opportunity.formFor(null), name: '【示例】拜访共同保存项目', amount: '25', stageIndex: 1, expected_close_date: '2026-12-20', quarters: [{year: 2026, quarter: 4, recognized: '10', collection: '5'}]};
  const mutation = w.opportunity.payload(form, null);
  const fields = {...run.result.fields, _quality_review_run_id: run.id, _opportunity_mutation: mutation};
  await assert.rejects(w.api.createVisit(customer.id, {...fields, follow_up_record: '更改后的正文，必须重审。'}), /已修改/);
  await assert.rejects(w.api.createVisit(customer.id, {...fields, _opportunity_mutation: {...mutation, quarterly_forecasts: []}}), /季度/);
  assert.equal((await w.api.listCustomerOpportunities(customer.id)).total, beforeOps.total);
  assert.equal((await w.api.listVisits({customer_id: customer.id})).total, beforeVisits.total);
  const saved = await w.api.createVisit(customer.id, fields);
  const afterOps = await w.api.listCustomerOpportunities(customer.id);
  const created = afterOps.items.find(row => row.id === saved.opportunity_id);
  assert.equal(afterOps.total, beforeOps.total + 1); assert.equal(created.name, mutation.name); assert.equal(created.amount, 250000);
  assert.equal(saved.fields.opportunity_id, created.id);
  const updatedFields = {...run.result.fields, follow_up_record: '客户已核对新一期试点边界，要求扩大验收清单。'};
  const updateRun = await review(w, customer.id, updatedFields);
  const update = w.opportunity.payload({...w.opportunity.formFor(created), amount: '30'}, created);
  const updateVisit = {...updatedFields, opportunity_id: created.id, _quality_review_run_id: updateRun.id, _opportunity_mutation: update};
  await assert.rejects(w.api.createVisit(customer.id, {...updateVisit, _opportunity_mutation: {...update, version_no: 0}}), /版本/);
  assert.equal((await w.api.listVisits({customer_id: customer.id})).total, beforeVisits.total + 1);
  assert.equal((await w.api.listCustomerOpportunities(customer.id)).items.find(row => row.id === created.id).amount, 250000);
  const updated = await w.api.createVisit(customer.id, updateVisit);
  const finalOps = await w.api.listCustomerOpportunities(customer.id), finalOp = finalOps.items.find(row => row.id === created.id);
  assert.equal(finalOps.total, afterOps.total); assert.equal(finalOp.amount, 300000); assert.equal(finalOp.version_no, created.version_no + 1);
  assert.equal(updated.opportunity_id, created.id);
});
