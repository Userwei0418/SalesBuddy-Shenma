require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../miniprogram/pages/customers/index.js');
const customer = id => ({ id, name: `隔离测试客户 ${id}`, opportunities: [], contacts: [], visits: [], tasks: [] });
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup(overrides = {}, hidden = false) {
  let page;
  const session = { workspaceId: 'isolated', userId: 'isolated-sales', role: 'sales' };
  const ui = { hidden, actions: [], errors: [], loading: false };
  const api = {
    getCustomerOverview: async id => customer(id),
    getCustomerHeader: async id => customer(id),
    ...overrides,
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('/apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    getApp: () => ({ globalData: { session }, guardPage: () => true, ensureLogin: () => true }),
    wx: {
      hideTabBar() { ui.hidden = true; ui.actions.push('hide'); },
      showTabBar() { ui.hidden = false; ui.actions.push('show'); },
      showLoading() { ui.loading = true; }, hideLoading() { ui.loading = false; },
      showToast(options) { ui.errors.push(options.title); }, hideNavigationBarLoading() {},
    },
    setTimeout, clearTimeout,
  });
  page.data = structuredClone(page.data);
  page.setData = (values, callback) => { Object.assign(page.data, values); if (callback) callback(); };
  // Exercise real overlay/return/request logic; skip unrelated map, history and paid advice loading.
  page.loadData = () => {};
  page.loadCustomerAdvice = () => {};
  page.loadDetailSections = () => {};
  return { page, ui };
}
function assertMap(h) {
  assert.equal(h.page.data.selectedCustomer, null);
  assert.equal(h.page.data.selectedBattleCustomer, null);
  assert.equal(h.page.data.plotCandidates.length, 0);
  assert.equal(h.ui.hidden, false);
}

test('客户卡片转完整详情失败后，地图可见且底部导航恢复', async () => {
  const h = setup({ getCustomerHeader: async () => { throw Error('模拟网络中断'); } });
  await h.page.showBattleCustomer('c1');
  assert.equal(h.ui.hidden, true);
  h.page.viewFullCustomer();
  await flush();
  assertMap(h);
  assert.deepEqual(h.ui.errors, ['模拟网络中断']);
  assert.equal(h.ui.loading, false);
});

test('完整详情切换客户卡片失败后，不遗留隐藏导航', async () => {
  const h = setup({ getCustomerOverview: async () => { throw Error('客户暂不可用'); } });
  await h.page.showCustomerDetail('c1');
  assert.equal(h.ui.hidden, true);
  await h.page.showBattleCustomer('c2');
  assertMap(h);
});

test('重新显示地图主动恢复导航，保留团队与象限筛选', () => {
  const h = setup({}, true);
  h.page.data.selectedTeam = 'team-selected';
  h.page.data.mapQuadrant = '主攻区';
  h.page.onShow();
  assertMap(h);
  assert.equal(h.page.data.selectedTeam, 'team-selected');
  assert.equal(h.page.data.mapQuadrant, '主攻区');
});

test('正常关闭卡片、完整详情及客户选择器后均恢复菜单', async () => {
  const h = setup();
  await h.page.showBattleCustomer('c1');
  assert.equal(h.ui.hidden, true);
  h.page.closeBattleCustomer(); assertMap(h);
  await h.page.showCustomerDetail('c1');
  assert.equal(h.ui.hidden, true);
  h.page.closeCustomer(); assertMap(h);
  h.page.data.plotCandidates = [{ id: 'c1' }, { id: 'c2' }];
  h.ui.hidden = true;
  h.page.closePlotCandidates(); assertMap(h);
});

test('另一个客户弹层仍在时，关闭旧弹层不能提前显示底部菜单', () => {
  const h = setup({}, true);
  h.page.data.selectedCustomer = customer('c1');
  h.page.closeBattleCustomer();
  assert.equal(h.ui.hidden, true);
  h.page.data.plotCandidates = [{ id: 'c2' }];
  h.page.closeCustomer();
  assert.equal(h.ui.hidden, true);
  h.page.closePlotCandidates(); assertMap(h);
});

for (const kind of ['battle', 'detail']) for (const outcome of ['resolve', 'reject']) {
  test(`${kind} 请求在关闭后迟到 ${outcome}，不能重开弹层或隐藏导航`, async () => {
    const wait = deferred();
    const h = setup({ [kind === 'battle' ? 'getCustomerOverview' : 'getCustomerHeader']: () => wait.promise });
    const request = h.page[kind === 'battle' ? 'showBattleCustomer' : 'showCustomerDetail']('c1');
    h.page[kind === 'battle' ? 'closeBattleCustomer' : 'closeCustomer']();
    const count = h.ui.actions.length;
    wait[outcome](outcome === 'resolve' ? customer('c1') : Error('旧请求失败'));
    await request;
    assertMap(h);
    assert.equal(h.ui.actions.length, count);
    assert.deepEqual(h.ui.errors, []);
  });
}

test('切页并重进后，旧客户响应不能重新隐藏地图导航', async () => {
  const wait = deferred();
  const h = setup({ getCustomerHeader: () => wait.promise });
  const request = h.page.showCustomerDetail('c1');
  h.page.onHide(); h.page.onShow();
  assertMap(h);
  wait.resolve(customer('c1')); await request;
  assertMap(h);
});

test('旧卡片请求失败不能干扰已打开的新客户详情', async () => {
  const wait = deferred();
  const h = setup({ getCustomerOverview: () => wait.promise });
  const older = h.page.showBattleCustomer('c1');
  await h.page.showCustomerDetail('c2');
  assert.equal(h.page.data.selectedCustomer.id, 'c2');
  assert.equal(h.ui.hidden, true);
  const count = h.ui.actions.length;
  wait.reject(Error('旧客户失败')); await older;
  assert.equal(h.page.data.selectedCustomer.id, 'c2');
  assert.equal(h.ui.hidden, true);
  assert.equal(h.ui.actions.length, count);
  assert.deepEqual(h.ui.errors, []);
});
