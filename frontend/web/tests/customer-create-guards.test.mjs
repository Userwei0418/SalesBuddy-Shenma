import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import * as flow from '../department-ui/pc/customer-flow.js';

function render({busy = false, optionsReady = true, search = {loading: false, error: '', value: {items: [], total: 0}}} = {}) {
  const calls = [], page = {data: {fields: [{key: 'customer_name', value: '全新客户'}], submitting: busy, creationOptionsReady: optionsReady, identityOptionsReady: optionsReady}};
  const module = {exports: {}};
  const react = {...React, useState: value => [value, () => {}], useEffect() {}};
  const data = {can: key => ['customer.claim_directory', 'customer.claim', 'customer.read'].includes(key), identity: () => 'actor-1', open: {route: value => calls.push(value)}};
  const require = name => name === 'react' ? react : name.endsWith('CustomerIdentityFields.jsx') ? {__esModule: true, default: 'CustomerIdentityFields'} : name.endsWith('customer-flow.js') ? flow : name.endsWith('/pc/data.js') ? data : name.endsWith('/pc/ui.jsx') ? {useAsync: () => search} : new Proxy({}, {get: (_, key) => key === '__esModule' ? true : String(key)});
  vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/CustomerCreate.jsx', import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code,
    {module, exports: module.exports, require, SalesRuntime: {current: page, businessOptions: () => ({customer: {}})}});
  const tree = module.exports.default({page, data: {...page.data, canRequestCreate: true, requestMode: true}, invoke: value => calls.push(value)});
  function find(node, type) {if (!node || typeof node !== 'object') return null; if (node.type === type) return node; for (const child of React.Children.toArray(node.props?.children)) {const match = find(child, type); if (match) return match;} return null;}
  return {page, calls, bar: find(tree, 'SbBottomBar').props};
}

test('creation submit requires a completed current-name search and ignores field changes before rerender', () => {
  const ready = render();
  assert.equal(ready.bar.primary.disabled, false);
  ready.bar.primary.onClick();
  assert.deepEqual(ready.calls, ['submitCustomer']);
  ready.page.data.fields[0].value = '刚刚改名';
  ready.bar.primary.onClick();
  assert.deepEqual(ready.calls, ['submitCustomer']);
  for (const search of [{loading: true, value: null}, {loading: false, error: '离线', value: null}, {loading: false, value: {items: [{id: 'c', name: '全新客户'}], total: 1}}]) {
    const blocked = render({search});
    assert.equal(blocked.bar.primary.disabled, true);
    blocked.bar.primary.onClick();
    assert.deepEqual(blocked.calls, []);
  }
});

test('submitting freezes form actions including a queued duplicate submit callback', () => {
  const busy = render({busy: true});
  assert.equal(busy.bar.primary.loading, true);
  assert.equal(busy.bar.primary.disabled, true);
  assert.equal(busy.bar.secondary.disabled, true);
  busy.bar.primary.onClick();
  assert.deepEqual(busy.calls, []);
});

test('missing creation catalog blocks submit including queued callbacks while preserving draft action', () => {
  const missing = render({optionsReady: false});
  assert.equal(missing.bar.primary.disabled, true);
  assert.equal(missing.bar.secondary.disabled, false);
  missing.bar.primary.onClick();
  assert.deepEqual(missing.calls, []);
  const changed = render();
  changed.page.data.creationOptionsReady = false;
  changed.bar.primary.onClick();
  assert.deepEqual(changed.calls, []);
});
