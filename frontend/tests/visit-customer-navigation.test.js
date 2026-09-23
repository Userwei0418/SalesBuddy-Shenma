const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  const storage = new Map([
    ['pendingOpenCustomerId', 'previous-customer'],
    ['pendingBattleCustomerId', 'previous-battle-customer'],
    ['pendingOpenOpportunityId', 'previous-opportunity'],
  ]);
  const navigation = [], toasts = [], details = [];
  const wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    switchTab: options => navigation.push(options),
    navigateTo() { assert.fail('Visit completion must use the map tab detail'); },
    showToast: options => toasts.push(options),
  };
  function load(name) {
    const filename = path.resolve(__dirname, `../miniprogram/pages/${name}/index.js`);
    let page;
    vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
      Page: definition => { page = definition; }, wx,
      require: name => name.endsWith('/apiClient') ? {} : require(path.resolve(path.dirname(filename), name)),
    });
    page.data = JSON.parse(JSON.stringify(page.data));
    return page;
  }
  const confirm = load('visit-confirm'), map = load('customers');
  map.showCustomerDetail = (...args) => details.push(args);
  map.showBattleCustomer = () => assert.fail('Stale compact map detail must not override this visit');
  return { confirm, map, storage, navigation, toasts, details };
}

test('归档后查看客户打开作战地图内同一客户的概览，清除旧客户和商机跳转上下文', () => {
  const h = harness();
  Object.assign(h.confirm.data, { archived: true, customerId: 'visit-customer', opportunityId: 'visit-opportunity' });
  h.confirm.openCustomer();
  assert.equal(h.navigation.length, 1);
  assert.equal(h.navigation[0].url, '/pages/customers/index');
  h.map.consumePendingCustomer();
  assert.deepEqual(h.details, [['visit-customer', '', 'overview']]);
  assert.equal(h.storage.size, 0);
  h.map.consumePendingCustomer();
  assert.equal(h.details.length, 1, 'Returning to the map must not reopen the completed visit detail');
});

test('缺少本次拜访客户时不跳转到缓存中的其他客户', () => {
  const h = harness();
  h.confirm.openCustomer();
  assert.equal(h.navigation.length, 0);
  assert.equal(h.toasts[0].title, '未找到本次拜访的客户');
});

test('切换作战地图失败后清除本次待打开客户，允许重试', () => {
  const h = harness();
  h.confirm.data.customerId = 'visit-customer';
  h.confirm.openCustomer();
  h.navigation[0].fail();
  h.map.consumePendingCustomer();
  assert.equal(h.details.length, 0);
  assert.equal(h.storage.size, 0);
  assert.equal(h.toasts[0].title, '打开客户详情失败，请重试');
  h.confirm.openCustomer();
  h.map.consumePendingCustomer();
  assert.deepEqual(h.details, [['visit-customer', '', 'overview']]);
});
