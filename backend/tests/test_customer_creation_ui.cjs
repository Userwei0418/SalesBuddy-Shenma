const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pathToFileURL} = require('node:url');

const assets = path.resolve(__dirname, '../src/sales_backend/web/assets');
const own = '10000000-0000-0000-0000-000000000001';
const external = '20000000-0000-0000-0000-000000000002';
const team = {id: '30000000-0000-0000-0000-000000000003', name: '审批目标团队'};
const copy = value => JSON.parse(JSON.stringify(value));
const base = {
  name: '合成企业', industry: '企业软件', customer_type: '潜在客户', level_code: 'Tier-1',
  source: '销售自拓', target_team: team.name, target_team_id: team.id, partner_name: '原伙伴名称',
  contact_name: '原联系人', contact_title: '原职位', contact_role: '使用者',
};

// Run the real page, inspect handler and onSubmit. Only UI binding, permissions
// and API transport are adapted in memory; renderers come from the real core.
async function harness(payload, {status = 'pending', reviewed, canCreate = true} = {}) {
  const core = await import(pathToFileURL(path.join(assets, 'core.js')).href);
  const row = {id: 'request/one', version_no: 7, status, payload, reviewed_payload: reviewed};
  const permissions = new Set(['customer.create_review', ...(canCreate ? ['customer.create'] : [])]);
  const calls = [];
  let handlers, opened;
  const context = {
    ...core, URLSearchParams, state: {filters: {}, offset: 0},
    hasPermission: permission => permissions.has(permission),
    requirePermission: permission => {if (!permissions.has(permission)) throw Error('permission denied');},
    api: async (route, init) => {
      if (!init) return {items: [row], total: 1};
      calls.push(copy({route, ...init}));
      return {status: init.body.decision};
    },
    readForDialog: async () => ({teams: [team]}),
    bindCommon: (_root, actions) => {handlers = actions;},
    dialog: (title, html, options) => {opened = {title, html, options};},
  };
  const source = fs.readFileSync(path.join(assets, 'customer-creation.js'), 'utf8')
    .replace(/^import[\s\S]*?from '\.\/core\.js';\n/, '')
    .replace('export async function customerCreationRequests', 'async function customerCreationRequests');
  vm.runInNewContext(source, context, {filename: 'customer-creation.js'});
  const page = await context.customerCreationRequests();
  page.bind({});
  await handlers.inspect(row.id);
  const form = new Map(Object.entries({...payload, decision: 'approved', reason: ' 审定通过 ',
    name: '修正企业名', contact_name: '修正联系人', target_team_id: team.id}));
  return {opened, calls, form, permissions, submit: () => opened.options.onSubmit(form, 'original-key')};
}

const cases = [
  {label: 'customer', business_identities: ['customer'], partner_profile_id: null, partner_id: external},
  {label: 'partner', business_identities: ['partner'], customer_type: null, partner_profile_id: own, partner_id: null},
  {label: 'both', business_identities: ['customer', 'partner'], partner_profile_id: own, partner_id: external},
];

for (const {label, ...identity} of cases) {
  test(`normal ${label} approval preserves identity and stable IDs while editing other fields`, async () => {
    const payload = {...base, ...identity};
    const original = copy(payload);
    const view = await harness(payload);
    const hasCustomer = identity.business_identities.includes('customer');
    assert.equal(view.opened.html.includes('name="customer_type"'), hasCustomer);
    assert.match(view.opened.html, /业务身份/);
    if (identity.partner_profile_id) assert.match(view.opened.html, new RegExp(own));
    if (identity.partner_id) assert.match(view.opened.html, new RegExp(external));
    assert.match(view.opened.html, /自身伙伴档案 ID/);
    assert.match(view.opened.html, /所属伙伴档案 ID/);
    // DOM/form injection cannot change the read-only identity or stable links.
    view.form.set('business_identities', ['customer']);
    view.form.set('partner_profile_id', external);
    view.form.set('partner_id', own);
    view.form.set('customer_type', '已成单客户');
    view.form.set('partner_name', '修正历史文本伙伴');
    await view.submit();
    assert.equal(view.calls.length, 1);
    const request = view.calls[0], approved = request.body.approved_payload;
    assert.equal(request.route, '/customer-creation-requests/request%2Fone/decision');
    assert.equal(request.method, 'POST');
    assert.equal(request.key, 'original-key');
    assert.equal(request.body.version_no, 7);
    assert.equal(request.body.reason, '审定通过');
    for (const field of ['business_identities', 'partner_profile_id', 'partner_id']) {
      assert.deepEqual(approved[field], identity[field]);
    }
    assert.equal(approved.customer_type, hasCustomer ? '已成单客户' : null);
    assert.equal(approved.name, '修正企业名');
    assert.equal(approved.contact_name, '修正联系人');
    assert.equal(approved.target_team_id, team.id);
    assert.equal(approved.target_team, team.name);
    assert.equal(approved.partner_name, identity.partner_id ? base.partner_name : '修正历史文本伙伴');
    assert.deepEqual(payload, original);
  });
}

test('legacy request without identity or stable IDs remains a customer and preserves editable text', async () => {
  const view = await harness({...base});
  view.form.set('partner_name', '修正旧文本');
  await view.submit();
  const approved = view.calls[0].body.approved_payload;
  assert.deepEqual(approved.business_identities, ['customer']);
  assert.equal(approved.partner_profile_id, null);
  assert.equal(approved.partner_id, null);
  assert.equal(approved.partner_name, '修正旧文本');
});

test('new partner profile is not replaced by the external partner ID', async () => {
  const view = await harness({...base, business_identities: ['customer', 'partner'], partner_id: external});
  await view.submit();
  const approved = view.calls[0].body.approved_payload;
  assert.equal(approved.partner_profile_id, null);
  assert.equal(approved.partner_id, external);
});

test('original and reviewed records each show their own identity and stable links', async () => {
  const view = await harness({...base, ...cases[1]}, {status: 'approved',
    reviewed: {...base, ...cases[2], name: '<reviewed>'}});
  const [original, reviewed] = view.opened.html.split('<h3>审定资料</h3>');
  assert.match(original, /<dd>合作伙伴<\/dd>/);
  assert.match(reviewed, /<dd>客户、合作伙伴<\/dd>/);
  assert.match(reviewed, /&lt;reviewed&gt;/);
  assert.match(reviewed, new RegExp(own));
  assert.match(reviewed, new RegExp(external));
  assert.equal(view.opened.options.onSubmit, undefined);
});

test('rejection keeps the original version guard and sends no approved payload', async () => {
  const view = await harness({...base, ...cases[1]}, {canCreate: false});
  view.form.set('decision', 'rejected');
  view.form.set('reason', ' ');
  await assert.rejects(view.submit(), /请填写驳回原因/);
  assert.equal(view.calls.length, 0);
  view.form.set('reason', ' 需补充资料 ');
  await view.submit();
  assert.deepEqual(view.calls[0].body, {decision: 'rejected', reason: '需补充资料', version_no: 7});
});

test('approval still checks effective permission and a currently selectable team at submit', async () => {
  const view = await harness({...base, ...cases[2]});
  view.permissions.delete('customer.create');
  await assert.rejects(view.submit(), /permission denied/);
  view.permissions.add('customer.create');
  view.form.set('target_team_id', 'outside-scope');
  await assert.rejects(view.submit(), /有效部门/);
  assert.equal(view.calls.length, 0);
});
