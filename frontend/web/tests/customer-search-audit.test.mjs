import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import {searchCustomers} from '../department-ui/pc/knowledge-data.js';

test('knowledge searches every keyword remotely, within customer.read, including an English customer beyond initial suggestions', async () => {
  const calls = [];
  const client = {listCustomers: async options => {
    calls.push(options);
    return {items: options.q ? [{id: 'beyond-50', name: 'ACME 合成客户'}] : Array.from({length: 50}, (_, i) => ({id: i, name: '初始客户' + i}))};
  }};
  globalThis.SalesRuntime = {require: () => client};
  assert.equal((await searchCustomers('')).length, 50);
  assert.equal((await searchCustomers(' ACME '))[0].id, 'beyond-50');
  await searchCustomers('跨越');
  assert.deepEqual(calls, [
    {q: '', pageSize: 50, businessIdentity: 'customer'},
    {q: 'ACME', pageSize: 50, businessIdentity: 'customer'},
    {q: '跨越', pageSize: 50, businessIdentity: 'customer'},
  ]);
});

test('knowledge rejects malformed lists and preserves permission/network errors without a directory fallback', async () => {
  let count = 0, result = {};
  globalThis.SalesRuntime = {require: () => ({listCustomers: async () => { count++; if (result instanceof Error) throw result; return result; }})};
  await assert.rejects(searchCustomers('跨越'), /客户列表暂不可用/);
  result = Object.assign(Error('当前无客户档案读取权限'), {statusCode: 403});
  await assert.rejects(searchCustomers('跨越'), error => error === result);
  result = Error('网络连接中断');
  await assert.rejects(searchCustomers('跨越'), error => error === result);
  assert.equal(count, 3);
  await assert.rejects(searchCustomers('字'.repeat(101)), /100/);
  assert.equal(count, 3, 'invalid keywords never reach the server');
  result = {items: []};
  assert.deepEqual(await searchCustomers('😀'.repeat(100)), []);
});

function component(file) {
  const source = transformSync(fs.readFileSync(new URL('../department-ui/' + file, import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code;
  const module = {exports: {}};
  vm.runInNewContext(source, {module, exports: module.exports, require: name => name === 'react' ? {...React, useEffect() {}}
    : name === './pc/data.js' ? {accessModule: () => ({visitImportAction: () => ({permission: null})}), can: () => false}
    : new Proxy({}, {get: (_, key) => key})});
  return module.exports.default;
}
const all = (node, predicate) => !React.isValidElement(node) ? [] : [
  ...(predicate(node) ? [node] : []), ...React.Children.toArray(node.props.children).flatMap(child => all(child, predicate)),
];
for (const [file, field] of [['VisitEntry.jsx', 'customerResults'], ['VisitConfirm.jsx', 'customers']]) {
  const View = component(file);
  const render = changes => {
    const calls = [];
    const tree = View({page: {}, data: {[field]: [{id: 'old', name: '旧结果'}], ...changes}, select: () => null,
      invoke: (name, event) => calls.push({name, event}), invokeOn() {}});
    return {tree, calls, rows: all(tree, node => node.type === 'SbListRow'), states: all(tree, node => node.type === 'SbStatePanel')};
  };
  test(`${file}: loading and error hide stale customers; a true empty result is distinct`, () => {
    const loading = render({searching: true});
    assert.equal(loading.rows.length, 0);
    assert.equal(loading.states[0].props.state, 'loading');
    const error = render({customerSearchError: '当前账号没有客户目录权限'});
    assert.equal(error.rows.length, 0);
    assert.equal(error.states[0].props.state, 'error');
    error.states[0].props.onRetry();
    assert.equal(error.calls[0].name, 'retryCustomerSearch');
    assert.equal(render({[field]: []}).states.length, 0, 'not yet searched is not a zero result');
    assert.equal(render({[field]: [], customerSearchDone: true}).states[0].props.state, 'empty');
  });
  test(`${file}: search action and selection reach the shared controller, without inventing a team`, () => {
    const view = render({customerSearchDone: true});
    all(view.tree, node => node.type === 'SbSearch')[0].props.onSearch();
    view.rows[0].props.onClick();
    assert.deepEqual(view.calls.map(row => row.name), ['retryCustomerSearch', 'chooseCustomer']);
    assert.equal(view.rows[0].props.summary, '');
    const busy = render({busy: true, isProcessing: true});
    assert.equal(busy.rows[0].props.onClick, undefined);
  });
}
