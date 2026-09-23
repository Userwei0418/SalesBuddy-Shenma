const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const pageFile = path.resolve(__dirname, '../miniprogram/pages/management-task-create/index.js');
const template = fs.readFileSync(pageFile.replace('.js', '.wxml'), 'utf8');

function harness() {
  let page;
  const modals = [], sent = [], toasts = [];
  const members = [
    { id: 'u1', name: '销售甲', account_code: 'sales-a', role: 'sales', team: '南区' },
    { id: 'u2', name: '销售乙', account_code: 'sales-b', role: 'sales', team: '北区' },
  ];
  const session = { userId: 'u1', workspaceId: 'w1', userName: '销售甲', role: 'sales' };
  const app = { ensureLogin: () => true, globalData: { session,
    roles: { sales: { name: '一线销售', scope: '本人' } } } };
  vm.runInNewContext(fs.readFileSync(pageFile, 'utf8'), {
    Page: value => { page = value; }, getApp: () => app, setTimeout() {},
    require: name => name.includes('apiClient') ? {
      listTaskRecipients: async () => ({ items: members }),
      createTask: async input => { sent.push(input); return { id: 'task-from-api' }; },
    } : require(path.resolve(path.dirname(pageFile), name)),
    wx: { showModal: options => modals.push(options), showToast: options => toasts.push(options),
      vibrateShort() {}, setStorageSync() {}, navigateBack() {} },
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = (patch, callback) => { Object.assign(page.data, patch); if (callback) callback(); };
  return { page, modals, sent, toasts };
}

// Exercise the actual template-to-native boundary. A native selector must receive
// an in-range cursor even while the page has no human-confirmed recipient.
function pickerState(page, handler) {
  const tag = template.match(new RegExp(`<picker\\b[^>]*bindchange="${handler}"[^>]*>`))[0];
  const bound = name => {
    const expression = tag.match(new RegExp(`${name}="\\{\\{([^]*?)\\}\\}"`))[1];
    return vm.runInNewContext(expression, { ...page.data });
  };
  return { cursor: bound('value'), range: bound('range'), disabled: bound('disabled') };
}

test('default first row can be confirmed without auto-assigning before user selection', async () => {
    const h = harness(); h.page.onLoad({}); await tick();
    h.page.inputDescription({ detail: { value: '请在截止时间前核对客户资料并反馈' } });
    const handler = 'changeAssignee';
    const selectedKey = 'selectedMember';
    const native = pickerState(h.page, handler);
    assert.equal(native.disabled, false);
    assert.ok(Number.isInteger(native.cursor) && native.cursor >= 0 && native.cursor < native.range.length,
      'the unopened native selector must not receive the business sentinel -1');
    assert.equal(h.page.data[selectedKey], null);
    h.page.submitTask();
    assert.equal(h.modals.length, 0, 'opening or cancelling a selector must not authorize a recipient');
    assert.equal(h.sent.length, 0);

    // The user confirms the displayed default row, without scrolling the wheel.
    h.page[handler]({ detail: { value: String(native.cursor) } });
    assert.equal(h.page.data[selectedKey], native.range[native.cursor]);
    h.page.submitTask();
    assert.equal(h.modals.length, 1);
    assert.equal(h.sent.length, 0, 'task submission still requires its confirmation dialog');
    h.modals[0].success({ confirm: false });
    assert.equal(h.sent.length, 0);
    h.page.submitTask(); h.modals[1].success({ confirm: true }); await tick();
    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0].targetPosition, null);
    assert.equal(h.sent[0].assigneeAccount, 'sales-a');
});

test('a selected colleague stays selected on reopen; the page has no position mode', async () => {
  const h = harness(); h.page.onLoad({}); await tick();
  h.page.changeAssignee({ detail: { value: '1' } });
  assert.equal(pickerState(h.page, 'changeAssignee').cursor, 1);
  assert.equal(h.page.data.selectedMember.id, 'u2');
  assert.doesNotMatch(template, /changePosition|changeTargetMode|按岗位/);
  assert.equal(h.sent.length, 0);
});
