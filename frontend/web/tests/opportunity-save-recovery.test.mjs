import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';

// Exercise the rendered Web controls and callbacks, using shared-page state as
// the input. The shared controller separately owns persistence and receipt I/O.
const source = transformSync(fs.readFileSync(new URL('../department-ui/OpportunityCreate.jsx', import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code;
const module = {exports: {}};
vm.runInNewContext(source, {
  module, exports: module.exports,
  require: name => name === 'react' ? React
    : name === './pc/data.js' ? {can: () => true, open: {route: () => {}}}
      : new Proxy({}, {get: (_, key) => key}),
});
const {default: OpportunityCreate, OpportunityForm, FdePicker} = module.exports;
function all(node, predicate) {
  if (!React.isValidElement(node)) return [];
  return [ ...(predicate(node) ? [node] : []), ...React.Children.toArray(node.props.children).flatMap(child => all(child, predicate)) ];
}
const textOf = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : React.isValidElement(node) ? React.Children.toArray(node.props.children).map(textOf).join('') : '';
function render(overrides = {}) {
  const calls = [], formCalls = [];
  const form = {data: {form: {name: '待核对的原商机草稿', amount: '100'}}, properties: {}};
  const picker = {data: {open: true, draft: [{id: 'fde-1', name: '成员'}]}};
  const data = {customerId: 'customer-1', customerName: '原客户', busy: false, loading: false, saveStatus: 'idle', createAccessAllowed: true, ...overrides};
  const page = {selectComponent: () => form};
  const tree = OpportunityCreate({
    page, data, select: id => id === '#opportunityForm' ? form : picker,
    invoke: (name, payload) => calls.push({name, payload}),
    invokeOn: (...args) => formCalls.push(args),
  });
  return {tree, data, page, form, calls, formCalls,
    primary: all(tree, node => node.type === 'SbBottomBar')[0]?.props.primary,
    displayedForm: all(tree, node => node.type === OpportunityForm)[0],
  };
}

test('ordinary editing still dispatches changes and saves through the shared controller', () => {
  const view = render();
  assert.equal(view.displayedForm.props.disabled, false);
  view.displayedForm.props.invokeOn(view.form, 'input', {detail: {value: '新名称'}});
  assert.equal(view.formCalls.length, 1);
  assert.equal(view.primary.label, '确认保存商机');
  view.primary.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['submit']);
});

for (const saveStatus of ['saving', 'unknown', 'checking', 'succeeded']) {
  test(`${saveStatus}: preserve the draft and block form changes in both React and shared components`, () => {
    const view = render({saveStatus});
    assert.equal(view.displayedForm.props.form, view.form);
    assert.equal(view.form.data.form.name, '待核对的原商机草稿');
    assert.equal(view.displayedForm.props.disabled, true);
    view.displayedForm.props.invokeOn(view.form, 'input', {detail: {value: '不能改名另建'}});
    view.displayedForm.props.invokeOn(view.form, 'choosePartner', {dataset: {id: 'partner-2'}});
    assert.equal(view.formCalls.length, 0, 'custom row and portal callbacks must also be guarded');
    assert.ok(OpportunityCreate.subcomponents(view.page, view.data).every(row => row.props.disabled));
    if (saveStatus === 'saving') view.primary.onClick();
    assert.equal(view.calls.length, 0);
  });
}

test('unknown result offers only original-result query and does not claim saving failed', () => {
  const view = render({saveStatus: 'unknown', error: '旧的网络失败提示', saveRecoveryMessage: '连接中断，原保存结果仍需核对。'});
  const banner = all(view.tree, node => node.props.role === 'status')[0];
  assert.match(textOf(banner), /保存结果待确认.*原保存结果仍需核对/);
  assert.equal(all(view.tree, node => node.props.role === 'alert').length, 0);
  assert.equal(view.primary.label, '查询保存结果');
  assert.equal(view.primary.disabled, false);
  view.primary.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['checkSaveResult']);
});

test('checking result disables repeat query without turning it into another save', () => {
  const view = render({saveStatus: 'checking'});
  assert.equal(view.primary.loading, true);
  assert.equal(view.primary.loadingLabel, '正在查询');
  assert.equal(view.primary.disabled, true);
  view.primary.onClick();
  assert.equal(view.calls.length, 0);
});

test('reopened unresolved operation remains queryable without a restored draft or customer details', () => {
  const view = render({saveStatus: 'unknown', customerId: '', customerName: '', customers: [{id: 'other', name: '另一客户'}]});
  assert.equal(view.displayedForm, undefined);
  const search = all(view.tree, node => node.type === 'SbSearch')[0];
  assert.equal(search.props.disabled, true);
  search.props.onChange('尝试切换客户');
  assert.equal(all(view.tree, node => node.type === 'SbListRow')[0].props.onClick, undefined);
  assert.equal(view.calls.length, 0);
  assert.equal(all(view.tree, node => node.props.title === '先在左侧选择客户').length, 0);
  view.primary.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['checkSaveResult']);
});

test('confirmed success opens the original saved record and never re-submits the old draft', () => {
  const view = render({saveStatus: 'succeeded', saveRecoveryOpportunityId: 'saved-original-id'});
  assert.equal(view.primary.label, '查看已保存商机');
  assert.equal(view.primary.disabled, false);
  view.primary.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['openSavedOpportunity']);
  const missingId = render({saveStatus: 'succeeded'});
  assert.equal(missingId.primary.disabled, true);
  missingId.primary.onClick();
  assert.equal(missingId.calls.length, 0, 'missing receipt identity cannot fall back to create');
});

test('deterministic rejection unlocks editing and retains its reason', () => {
  const view = render({saveStatus: 'rejected', error: '名称校验未通过，请修改后提交。'});
  assert.equal(view.displayedForm.props.disabled, false);
  assert.match(textOf(all(view.tree, node => node.props.role === 'alert')[0]), /名称校验未通过/);
  view.displayedForm.props.invokeOn(view.form, 'input', {detail: {value: '修正名称'}});
  view.primary.onClick();
  assert.equal(view.formCalls.length, 1);
  assert.deepEqual(view.calls.map(row => row.name), ['submit']);
});

test('only an in-memory original payload enables explicit same-operation retry', () => {
  const view = render({saveStatus: 'unknown', canRetrySave: true});
  all(view.tree, node => node.type === 'Button' && textOf(node) === '按原内容重试')[0].props.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['retrySave']);
  assert.equal(view.formCalls.length, 0, 'retry must not read, prepare or change the visible draft');
  const reopened = render({saveStatus: 'unknown', canRetrySave: false});
  assert.equal(all(reopened.tree, node => node.type === 'Button' && textOf(node) === '按原内容重试').length, 0);
});

test('ending an unresolved submission delegates server confirmation and remains locked while waiting', () => {
  const view = render({saveStatus: 'unknown'});
  all(view.tree, node => node.type === 'Button' && textOf(node) === '结束未完成的提交')[0].props.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['cancelSave']);
  assert.equal(view.displayedForm.props.disabled, true);
  const pending = render({saveStatus: 'checking', saveRecoveryAction: 'cancel', busy: true});
  assert.match(textOf(all(pending.tree, node => node.props.role === 'status')[0]), /正在结束未完成的提交/);
  assert.equal(pending.primary.loadingLabel, '正在结束提交');
  pending.primary.onClick();
  assert.equal(pending.calls.length, 0);
  assert.equal(pending.displayedForm.props.disabled, true);
});

test('confirmed server cancellation shows a neutral completion message and allows a new intent', () => {
  const view = render({saveStatus: 'rejected', saveRecoveryMessage: '已安全终止原提交，可以修改后重新保存。'});
  assert.match(textOf(all(view.tree, node => node.props.role === 'status')[0]), /已安全终止原提交/);
  assert.equal(all(view.tree, node => node.props.role === 'alert').length, 0);
  assert.equal(view.displayedForm.props.disabled, false);
  view.primary.onClick();
  assert.deepEqual(view.calls.map(row => row.name), ['submit']);
});

test('busy unresolved recovery cannot fire either retry or cancellation again', () => {
  const view = render({saveStatus: 'unknown', busy: true, canRetrySave: true});
  const buttons = all(view.tree, node => node.type === 'Button' && ['按原内容重试', '结束未完成的提交'].includes(textOf(node)));
  assert.equal(buttons.length, 2);
  for (const button of buttons) {
    assert.equal(button.props.disabled, true);
    button.props.onClick();
  }
  assert.equal(view.calls.length, 0);
});

test('reloading customer details does not offer recovery actions the shared page cannot yet execute', () => {
  const view = render({saveStatus: 'unknown', loading: true, canRetrySave: true});
  assert.equal(view.primary.disabled, true);
  view.primary.onClick();
  for (const button of all(view.tree, node => node.type === 'Button' && ['按原内容重试', '结束未完成的提交'].includes(textOf(node)))) {
    assert.equal(button.props.disabled, true);
    button.props.onClick();
  }
  assert.equal(view.calls.length, 0);
});

test('legacy busy state and customer selection respect the same write lock', () => {
  const view = render({busy: true});
  assert.equal(view.displayedForm.props.disabled, true);
  assert.equal(view.primary.loading, true);
  view.primary.onClick();
  assert.equal(view.calls.length, 0);
  const choose = render({customerId: '', customerName: '', customers: [{id: 'customer-2', name: '客户二'}]});
  all(choose.tree, node => node.type === 'SbSearch')[0].props.onChange('客户二');
  all(choose.tree, node => node.type === 'SbListRow')[0].props.onClick();
  assert.deepEqual(choose.calls.map(row => row.name), ['inputCustomer', 'selectCustomer']);
});

test('a picker opened before save cannot mutate the draft after the form locks', () => {
  const calls = [];
  const tree = FdePicker({picker: {data: {open: true, draft: [], rows: []}}, disabled: true, invokeOn: (...args) => calls.push(args)});
  const sheet = all(tree, node => node.type === 'SbSheet')[0];
  assert.equal(sheet.props.open, false);
  for (const button of all(sheet.props.footer, node => node.type === 'Button')) button.props.onClick();
  sheet.props.onClose();
  assert.equal(calls.length, 0);
});

test('customer search distinguishes loading, actual empty result, and a failed directory request', () => {
  const chooser = {customerId: '', customerName: '', query: '跨越', customers: []};
  const loading = render({...chooser, customerSearchLoading: true});
  assert.match(textOf(loading.tree), /正在搜索客户/);
  assert.doesNotMatch(textOf(loading.tree), /未找到/);
  const empty = render({...chooser, customerSearchDone: true});
  assert.match(textOf(empty.tree), /未找到“跨越”相关客户/);
  const failed = render({...chooser, customerSearchError: '客户目录暂不可用'});
  assert.match(textOf(all(failed.tree, node => node.props.role === 'alert')[0]), /客户目录暂不可用/);
  assert.doesNotMatch(textOf(failed.tree), /未找到/);
  all(failed.tree, node => node.type === 'Button' && textOf(node) === '重新搜索')[0].props.onClick();
  assert.deepEqual(failed.calls.map(row => row.name), ['webSearchCustomers']);
});

test('initial suggestions expose loading and failure, and Enter/search icon is wired', () => {
  const chooser = {customerId: '', customerName: '', customers: []};
  assert.match(textOf(render({...chooser, webCustomerLoading: true}).tree), /正在加载客户/);
  const failed = render({...chooser, webCustomerError: '无法读取客户目录'});
  assert.match(textOf(failed.tree), /无法读取客户目录/);
  all(failed.tree, node => node.type === 'SbSearch')[0].props.onSearch('跨越');
  assert.equal(failed.calls[0].name, 'webSearchCustomers');
  assert.equal(failed.calls[0].payload.detail.value, '跨越');
  const locked = render({...chooser, saveStatus: 'unknown', webCustomerError: '原目录请求失败'});
  all(locked.tree, node => node.type === 'SbSearch')[0].props.onSearch('跨越');
  all(locked.tree, node => node.type === 'Button' && textOf(node) === '重新搜索')[0].props.onClick();
  assert.equal(locked.calls.length, 0, 'new search must preserve unresolved-save locking');
});


test('known creation denial keeps the form draft but disables submission and offers capability recheck',()=>{
 const view=render({createAccessAllowed:false,error:'该客户的认领人不在你可创建商机的范围内，请更换客户'});
 assert.equal(view.primary.disabled,true);view.primary.onClick();assert.equal(view.calls.length,0);
 assert.equal(view.displayedForm.props.form,view.form);assert.match(textOf(view.tree),/重新核对创建资格/);
});
