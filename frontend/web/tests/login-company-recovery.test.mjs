import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';

const source = transformSync(fs.readFileSync(new URL('../department-ui/Login.jsx', import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code;
const module = {exports: {}};
const Input = Object.assign(() => null, {Password: () => null});
vm.runInNewContext(source, {module, exports: module.exports, require: name => {
  if (name === 'react') return {...React, useEffect() {}, useRef: () => ({current: null})};
  if (name === './ImeInput.jsx') return {Input};
  if (name === 'antd') return {Alert: 'Alert', Button: 'Button', Checkbox: 'Checkbox', Space: Object.assign(() => null, {Compact: 'Compact'})};
  return {SbField: 'Field'};
}});
function render(data, invoke, page = {}) {
  const calls = [];
  const tree = module.exports.default({page, data, invoke: (...args) => { calls.push(args); return invoke?.(...args); }});
  function all(node) { return node && typeof node === 'object' ? [node, ...React.Children.toArray(node.props?.children).flatMap(all)] : []; }
  return {nodes: all(tree), calls};
}
const copy = node => node == null || typeof node === 'boolean' ? '' : typeof node === 'object' ? React.Children.toArray(node.props?.children).map(copy).join(' ') : String(node);
const button = (view, label) => view.nodes.find(node => node.type === 'Button' && copy(node) === label);

test('failed explicit company link exposes recovery without a company-code input', () => {
  const data = {workspace: 'missing-company', account: 'SYNTHETIC_ACCOUNT', companyState: 'error', companyError: '未找到可用公司，请核对公司码', showCompany: false, editingCompany: false};
  const {nodes, calls} = render(data, undefined, {_entryExplicit: true});
  assert.equal(nodes.find(node => node.type === 'Alert').props.title, data.companyError);
  assert.equal(nodes.some(node => node.props?.id === 'login-company'), false);
  const recover = nodes.find(node => node.type === 'Button' && node.props.children === '返回通用登录');
  assert.ok(recover);
  assert.equal(calls.length, 0, 'rendering an error must not retry login or change company');
  recover.props.onClick();
  assert.equal(calls[0][0], 'useSimpleLogin', 'reuse the existing guarded login recovery');
});

test('general login has account and phone modes without a manual company entry', () => {
  const view = render({loginType: 'auto', loginHelp: false});
  assert.equal(button(view, '使用公司入口'), undefined);
  button(view, '更多登录方式').props.onClick();
  assert.deepEqual(view.calls.map(([name]) => name), ['toggleLoginHelp']);
  const help = render({loginType: 'phone', loginHelp: true});
  assert.equal(help.nodes.find(node => node.type === Input).props.placeholder, '请输入已绑定的手机号');
  assert.equal(button(help, '手机号登录').props['aria-pressed'], true);
  button(help, '手机号登录').props.onClick();
  button(help, '账号登录').props.onClick();
  assert.deepEqual(help.calls.map(([name]) => name), ['usePhoneLogin', 'useAccountLogin']);
  const numeric = render({numericAccount: true, loginType: 'auto'});
  button(numeric, '按账号登录').props.onClick();
  assert.equal(numeric.calls[0][0], 'useAccountLogin');
  assert.equal(button(render({numericAccount: true, loginType: 'account'}), '按账号登录'), undefined);
});

test('remembered company is hidden while account memory and its explicit forgetting remain available', () => {
  const data = {workspace: 'remembered-company', account: 'SYNTHETIC_ACCOUNT', companyState: 'legacy', rememberAccount: true};
  const view = render(data);
  assert.equal(view.nodes.some(node => node.props?.id === 'login-company' || node.props?.className === 'ds-company-entry'), false);
  assert.ok(view.nodes.some(node => node.type === 'Checkbox' && copy(node).includes('记住账号')));
  assert.equal(view.calls.length, 0);
  button(view, '忘记此账号的本机记忆').props.onClick();
  assert.deepEqual(view.calls.map(([name]) => name), ['forgetRememberedPassword']);
  for (const state of [{loading: true}, {mustChangePassword: true}, {companySwitchRequired: true}]) {
    const locked = render({...data, loginHelp: true, numericAccount: true, ...state});
    for (const label of ['更多登录方式', '收起登录方式', '手机号登录', '账号登录', '使用公司入口', '按账号登录', '忘记此账号的本机记忆']) {
      const control = button(locked, label);
      assert.ok(!control || control.props.disabled, label);
    }
    assert.equal(locked.calls.length, 0);
  }
});

// Exercise the actual imported Mini page through Web button events. The API
// boundary is synthetic; these cases never use a browser, network or real login.
function sharedPage(saved) {
  const base = 'https://synthetic.invalid/api/v1', storage = new Map(), attempts = [], lookups = [];
  if (saved) storage.set('salesAccountHistoryV3', {version: 3, entries: [{baseUrl: base, ...saved}]});
  const wx = {getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key), showToast() {}, nextTick: fn => fn()};
  function utility(name) {
    const target = {exports: {}};
    vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/' + name + '.js', import.meta.url), 'utf8'), {module: target, exports: target.exports, wx});
    return target.exports;
  }
  const api = {getBaseUrl: () => base, getCompanyEntry: async code => { lookups.push(code); return {company_code: code, company_name: '合成公司'}; }};
  const dependencies = {apiClient: api, rememberedLogin: utility('rememberedLogin'), companyEntry: utility('companyEntry'), encryptedCredentials: {available: () => false, read: async () => null, clear: async () => true}};
  const app = {globalData: {session: null}, loginWithApi: async (...args) => { attempts.push(args); throw Object.assign(new Error('synthetic rejection'), {statusCode: 401}); }};
  let definition;
  vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/pages/login/index.js', import.meta.url), 'utf8'), {Page: value => { definition = value; }, require: name => dependencies[name.split('/').at(-1)], wx, getApp: () => app});
  const page = {...definition, data: JSON.parse(JSON.stringify(definition.data)), setData(values) { Object.assign(this.data, values); }};
  page.onLoad();
  const view = () => render(page.data, (name, {detail = {}, dataset = {}} = {}) => page[name]({detail, currentTarget: {dataset}}), page);
  return {page, view, storage, attempts, lookups};
}

test('Web explicit phone mode drops only implicit company scope and sends the shared phone login intent', async () => {
  const h = sharedPage({workspace: 'old-company', account: 'OLD_ACCOUNT', loginType: 'account'});
  const rememberedBefore = JSON.stringify(h.storage.get('salesAccountHistoryV3'));
  button(h.view(), '更多登录方式').props.onClick();
  button(h.view(), '手机号登录').props.onClick();
  const field = h.view().nodes.find(node => node.type === Input && node.props.autoComplete === 'username');
  field.props.onChange({target: {value: '13900000000'}});
  Object.assign(h.page.data, {password: 'synthetic-test-password', agreed: true});
  await button(h.view(), '进入工作空间').props.onClick();
  assert.equal(h.attempts.length, 1);
  assert.equal(h.attempts[0][1], '13900000000');
  assert.equal(h.attempts[0][3], undefined);
  assert.equal(h.attempts[0][4], 'phone');
  assert.equal(h.lookups.length, 0);
  assert.equal(JSON.stringify(h.storage.get('salesAccountHistoryV3')), rememberedBefore);
});

test('Web explicit company is preserved for phone login and demo account login needs no company selection', async () => {
  const h = sharedPage();
  await h.page.applyCompanyEntry({code: 'explicit-company'});
  assert.equal(h.view().nodes.some(node => node.props?.id === 'login-company'), false);
  button(h.view(), '更多登录方式').props.onClick();
  button(h.view(), '手机号登录').props.onClick();
  Object.assign(h.page.data, {account: '13900000000', password: 'synthetic-test-password', agreed: true});
  await button(h.view(), '进入工作空间').props.onClick();
  assert.equal(h.attempts[0][3], 'explicit-company');
  assert.equal(h.attempts[0][4], 'phone');
  const demo = sharedPage();
  button(demo.view(), '更多登录方式').props.onClick();
  button(demo.view(), '账号登录').props.onClick();
  Object.assign(demo.page.data, {account: 'xs001', password: 'synthetic-test-password', agreed: true});
  await button(demo.view(), '进入工作空间').props.onClick();
  assert.equal(demo.attempts.length, 1);
  assert.equal(demo.attempts[0][1], 'XS001');
  assert.equal(demo.attempts[0][3], undefined);
  assert.equal(demo.attempts[0][4], 'account');
  assert.equal(demo.lookups.length, 0);
});

test('returning to general login does not erase memory and explicit forgetting removes only the displayed record', async () => {
  const h = sharedPage({workspace: 'old-company', account: 'XS001', loginType: 'account'});
  const history = h.storage.get('salesAccountHistoryV3');
  history.entries.unshift({...history.entries[0], workspace: 'other-company', account: 'OTHER_ACCOUNT'});
  const before = JSON.stringify(history);
  await h.page.applyCompanyEntry({code: 'explicit-company'});
  button(h.view(), '返回通用登录').props.onClick();
  assert.equal(h.page.data.workspace, '');
  assert.equal(JSON.stringify(h.storage.get('salesAccountHistoryV3')), before);
  assert.equal(h.attempts.length, 0);
  const forget = sharedPage({workspace: 'old-company', account: 'XS001', loginType: 'account'});
  const records = forget.storage.get('salesAccountHistoryV3');
  records.entries.unshift({...records.entries[0], workspace: 'other-company', account: 'OTHER_ACCOUNT'});
  await button(forget.view(), '忘记此账号的本机记忆').props.onClick();
  assert.equal(forget.page.data.workspace, '');
  assert.equal(forget.page.data.rememberAccount, false);
  assert.deepEqual(Array.from(forget.storage.get('salesAccountHistoryV3').entries, row => row.account), ['OTHER_ACCOUNT']);
  assert.equal(forget.attempts.length, 0);
});

test('company recovery cannot bypass a pending company switch, password change or login request', () => {
  for (const state of [{companySwitchRequired: true}, {mustChangePassword: true}, {loading: true}]) {
    const {nodes, calls} = render({workspace: 'other-company', companyState: 'error', companyError: '公司信息暂时无法确认', ...state}, undefined, {_entryExplicit: true});
    const recover = nodes.find(node => node.type === 'Button' && node.props.children === '返回通用登录');
    assert.ok(!recover || recover.props.disabled);
    assert.equal(calls.length, 0);
  }
  const normal = render({companyState: 'legacy', account: '', companyError: ''});
  assert.equal(normal.nodes.some(node => node.props?.id === 'login-company'), false);
  const switched = render({companyState: 'legacy', account: '', workspace: '', companyError: '上一家公司的旧错误'});
  assert.equal(switched.nodes.some(node => node.type === 'Alert'), false, 'switching accounts must not revive a stale company error');
  assert.equal(switched.nodes.some(node => node.props?.id === 'login-company'), false);
});
