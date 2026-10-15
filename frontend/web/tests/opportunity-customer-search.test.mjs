import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../review-forms.js', import.meta.url), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const requests = [], inputs = [], pending = [];
  const page = {
    route: 'pages/opportunity-create/index', data: {query: '', customers: [], customerId: ''}, searchSerial: 0,
    setData(change) { Object.assign(this.data, change); },
    // The shared controller owns keyword search. Here we verify that the Web
    // bridge delegates it, and cannot let the initial request overwrite it.
    inputCustomer(event) {
      inputs.push(event.detail.value); this.searchSerial++;
      this.setData({query: event.detail.value, customers: []});
    },
  };
  const runtime = {current: page, app: {globalData: {session: {workspaceId: 'demo', userId: 'sales', permissionVersion: 1}}}};
  const context = {SalesRuntime: runtime, queueMicrotask: callback => pending.push(callback)};
  vm.runInNewContext(source, context);
  const api = {customerSearchIdentity: () => JSON.stringify(runtime.app.globalData.session), listOpportunityCustomerReferences(options) {
    const flight = deferred(); requests.push({options, ...flight}); return flight.promise;
  }};
  context.SalesReviewForms.configure(page, api);
  return {page, requests, inputs, runtime, start: () => pending.splice(0).forEach(callback => callback()),
    configure: () => context.SalesReviewForms.configure(page, api)};
}

test('Web initial suggestions use the same dedicated customer reference helper, without auto-selecting', async () => {
  const f = fixture(); f.start(); f.configure();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].options.pageSize, 5);
  assert.equal(f.page.data.webCustomerLoading, true);
  f.requests[0].resolve({items: [{id: 'unclaimed', name: '跨越合成客户'}]}); await tick();
  assert.equal(f.page.data.customers[0].id, 'unclaimed');
  assert.equal(f.page.data.customerId, '');
  assert.equal(f.page.data.webCustomerLoading, false);
  assert.equal(f.page.data.webCustomerDone, true);
  assert.equal(f.page.customerSearchIdentity, JSON.stringify(f.runtime.app.globalData.session));
});

test('malformed directory data is not reported as an empty customer list', async () => {
  const f = fixture(); f.start();
  f.requests[0].resolve({}); await tick();
  assert.equal(f.page.data.webCustomerDone, false);
  assert.match(f.page.data.webCustomerError, /暂不可用/);
});

test('directory failure remains a retryable error instead of appearing as zero search results', async () => {
  const f = fixture(); f.start();
  f.requests[0].reject(new Error('当前账号无客户引用权限')); await tick();
  assert.equal(f.page.data.webCustomerError, '当前账号无客户引用权限');
  assert.equal(f.page.data.webCustomerDone, false);
  assert.equal(f.requests.length, 1, 'do not fall back to another directory after denial');
  f.page.webSearchCustomers();
  assert.equal(f.requests.length, 2);
  assert.equal(f.page.data.webCustomerError, '');
  f.requests[1].resolve({items: []}); await tick();
  assert.equal(f.page.data.webCustomerDone, true);
  assert.equal(f.page.data.webCustomerLoading, false);
});

for (const change of ['query', 'select', 'account', 'permissions', 'navigation', 'unload']) {
  test(`late initial suggestions cannot overwrite ${change}`, async () => {
    const f = fixture(); f.start();
    if (change === 'query') f.page.inputCustomer({detail: {value: '跨越'}});
    if (change === 'select') f.page.data.customerId = 'already-selected';
    if (change === 'account') f.runtime.app.globalData.session.userId = 'someone-else';
    if (change === 'permissions') f.runtime.app.globalData.session.permissionVersion++;
    if (change === 'navigation') f.runtime.current = {};
    if (change === 'unload') f.page._destroyed = true;
    f.requests[0].resolve({items: [{id: 'stale', name: '旧结果'}]}); await tick();
    assert.equal(f.page.data.customers.length, 0);
  });
}

test('search icon and retry delegate the keyword to the shared search controller', () => {
  const f = fixture(); f.start();
  f.page.webSearchCustomers({detail: {value: '跨越'}});
  f.page.webSearchCustomers();
  assert.deepEqual(f.inputs, ['跨越', '跨越']);
  assert.equal(f.requests.length, 1, 'keyword requests are owned by shared controller, not duplicated by Web');
});

test('clearing the keyword restores initial suggestions, including whitespace-only input', async () => {
  const f = fixture(); f.start();
  f.page.inputCustomer({detail: {value: '跨越'}});
  f.page.inputCustomer({detail: {value: '  '}});
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({items: [{id: 'fresh', name: '新初始结果'}]}); await tick();
  f.requests[0].resolve({items: [{id: 'stale', name: '旧初始结果'}]}); await tick();
  assert.equal(f.page.data.customers[0].id, 'fresh');
});

test('route-provided customer does not trigger a competing initial directory request', () => {
  const f = fixture(); f.page.data.customerId = 'from-route'; f.start();
  f.page.webSearchCustomers({detail: {value: '跨越'}});
  assert.equal(f.requests.length, 0);
  assert.equal(f.inputs.length, 0);
});
