const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const customer = i => ({ id: String(i), name: '客户' + i, can_claim: true });
const directory = (items, total = items.length, offset = 0) => ({ items, total,
  has_more: offset + items.length < total, next_offset: offset + items.length < total ? offset + items.length : null });

test('实际API封装编码认领查询和分页参数，旧company/department调用保留原契约', async () => {
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  const module = { exports: {} }, requests = [], storage = new Map();
  const wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), request: request => {
      requests.push(request); request.success({ statusCode: 200, data: directory([]) });
    } };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, wx, Map, Set, Date, require: name => require(path.resolve(path.dirname(filename), name)),
  });
  const api = module.exports;
  await api.listCustomerClaimPool({ q: '客户 & %_', pageSize: 50, offset: 100 });
  let url = new URL(requests.at(-1).url);
  assert.equal(url.pathname, '/api/v1/customers/claim-pool');
  assert.equal(url.searchParams.get('q'), '客户 & %_'); assert.equal(url.searchParams.get('offset'), '100');
  await api.listCustomerClaimPool({q: 'ST', industry: '软件 & 服务', claimStatus: 'pending'});
  url = new URL(requests.at(-1).url);
  assert.equal(url.searchParams.get('industry'), '软件 & 服务');
  assert.equal(url.searchParams.get('claim_status'), 'pending');
  await api.listCustomerClaimOptions();
  assert.equal(new URL(requests.at(-1).url).pathname, '/api/v1/customers/claim-pool/options');
  for (const scope of ['company', 'department']) {
    await api.listCustomers({ scope, q: '旧调用' }); url = new URL(requests.at(-1).url);
    assert.equal(url.pathname, '/api/v1/customers'); assert.equal(url.searchParams.get('scope'), scope);
    assert.equal(url.searchParams.get('page_size'), '100'); assert.equal(url.searchParams.has('offset'), false);
  }
});
const filterOptions = () => ({ industries: [{value: '', label: '全部行业'}, {value: '软件', label: '软件'}, {value: '金融', label: '金融'}],
  claim_statuses: [{value: '', label: '全部认领状态'}, {value: 'unclaimed', label: '未认领'}, {value: 'pending', label: '我的申请待审批'}] });
function pageFor(api) {
  api = { listCustomerClaimOptions: async () => filterOptions(), ...api };
  let definition, timerId = 0;
  const toasts = [], store = new Map(), timers = new Map();
  const app = { ensureLogin: () => true, globalData: { role: 'sales', session: {
    workspaceId: 'w', userId: 'sales', teamIds: ['team'], loginAt: 1, permissionVersion: 'v1', scope: 'self',
  } } };
  const wx = { showToast: x => toasts.push(x.title), showModal: x => x.success({ confirm: true }),
    setStorageSync: (k, v) => store.set(k, v) };
  vm.runInNewContext(fs.readFileSync(__dirname + '/../miniprogram/pages/customer-claim/index.js', 'utf8'), {
    require: name => name.includes('apiClient') ? api : { normalizeCustomerSummary: x => x },
    Page: x => definition = x, wx, getApp: () => app,
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
  });
  const page = { ...definition, disposed: false, data: JSON.parse(JSON.stringify(definition.data)),
    setData(x) { Object.assign(this.data, x); } };
  return { page, toasts, store, app, wx, runSearch: () => {
    const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback());
  } };
}

test('首次加载与onShow不重复请求；同账号返回保留搜索、刷新审批状态并清除选择', async () => {
  const calls = []; let approved = false;
  const { page } = pageFor({ listCustomerClaimPool: async options => {
    calls.push({ ...options });
    return directory([{ ...customer('c'), claimed: approved, can_claim: !approved }]);
  } });
  page.onLoad(); page.onShow(); await tick();
  assert.equal(calls.length, 1);
  await page.loadCustomers('客户');
  page.selectCustomer({ currentTarget: { dataset: { id: 'c' } } });
  page.setData({ resultMessage: '旧申请待审批' });
  page.onHide(); approved = true; await page.onShow();
  assert.equal(calls.length, 3);
  assert.deepEqual(calls.at(-1), { q: '客户', pageSize: 50, offset: 0 });
  assert.equal(page.data.selectedCustomerId, ''); assert.equal(page.selectedCustomer, null);
  assert.equal(page.data.resultMessage, '');
  assert.equal(page.data.customers[0].claimLabel, '本人已认领');
  assert.equal(page.data.customers[0].claimEligible, false);
  page.onShow(); assert.equal(calls.length, 3);
});

test('隐藏前的申请回执晚返回不能覆盖新审批结果，也不恢复待审批提示', async () => {
  const late = deferred(); let approved = false;
  const { page, toasts } = pageFor({
    listCustomerClaimPool: async () => directory([{ ...customer('c'), claimed: approved, can_claim: !approved }]),
    claimCustomer: () => late.promise,
  });
  await page.loadCustomers(); page.selectCustomer({ currentTarget: { dataset: { id: 'c' } } });
  page.confirmClaim(); assert.equal(page.data.submitting, true);
  page.onHide(); approved = true; await page.onShow();
  late.resolve({ status: 'pending' }); await tick();
  assert.equal(page.data.submitting, false); assert.equal(page.data.resultMessage, '');
  assert.equal(page.data.customers[0].claimLabel, '本人已认领');
  assert.equal(toasts.length, 0);
});

test('隐藏前的旧目录响应不能覆盖返回后已被他人认领的新名单', async () => {
  const late = deferred(); let calls = 0;
  const { page } = pageFor({ listCustomerClaimPool: () => ++calls === 1 ? late.promise
    : Promise.resolve(directory([{ ...customer('c'), can_claim: false, ownership_state: 'claimed' }])) });
  const before = page.loadCustomers(); page.onHide(); await page.onShow();
  late.resolve(directory([customer('c')])); await before;
  assert.equal(page.data.customers[0].claimEligible, false);
  assert.equal(page.data.customers[0].claimLabel, '已被认领');
});

test('首屏只请求50条、总数取完整目录；超过100家可连续浏览且并发触底不重复', async () => {
  const items = Array.from({ length: 121 }, (_, i) => customer(i));
  const calls = [], second = deferred();
  const { page } = pageFor({ listCustomerClaimPool: async options => {
    calls.push({ ...options });
    if (options.offset === 50) await second.promise;
    return directory(items.slice(options.offset, options.offset + 50), 121, options.offset);
  } });
  await page.loadCustomers('');
  assert.deepEqual(calls, [{ q: '', pageSize: 50, offset: 0 }]);
  assert.equal(page.data.total, 121); assert.equal(page.data.customers.length, 50);
  const more = page.loadMore(); page.onReachBottom(); page.loadMore();
  assert.equal(calls.length, 2); assert.equal(page.data.customers.length, 50);
  second.resolve(); await more; await page.onReachBottom();
  assert.deepEqual(calls.map(call => call.offset), [0, 50, 100]);
  assert.equal(page.data.customers.length, 121); assert.equal(new Set(page.data.customers.map(c => c.id)).size, 121);
  assert.equal(page.data.hasMore, false); assert.equal(page.data.nextOffset, null);
  await page.loadMore(); assert.equal(calls.length, 3);
});

test('认领名单保留待审批、本人、同事和可认领状态，不用可认领数量冒充总数', async () => {
  const items = [{ id: 'free', name: '未认领', can_claim: true },
    { id: 'pending', name: '待审批', can_claim: true, claim_status: 'pending' },
    { id: 'owned', name: '同事客户', can_claim: false, ownership_state: 'claimed' }];
  const { page } = pageFor({ listCustomerClaimPool: async () => directory(items) });
  await page.loadCustomers();
  assert.equal(page.data.total, 3); assert.equal(page.data.customers[0].claimEligible, true);
  for (const id of ['pending', 'owned']) {
    page.selectCustomer({ currentTarget: { dataset: { id } } });
    assert.equal(page.data.selectedCustomerId, '');
  }
});

test('第二页失败保留列表、总数及位置，显式重试同一页；真实空列表显示0', async () => {
  let attempts = 0;
  const { page } = pageFor({ listCustomerClaimPool: async ({ offset }) => {
    if (offset && ++attempts === 1) throw new Error('合成超时');
    return directory(Array.from({ length: offset ? 1 : 50 }, (_, i) => customer(i + offset)), 51, offset);
  } });
  await page.loadCustomers(); await page.loadMore();
  assert.equal(page.data.customers.length, 50); assert.equal(page.data.total, 51);
  assert.equal(page.data.nextOffset, 50); assert.match(page.data.loadMoreError, /超时/);
  page.onReachBottom(); assert.equal(attempts, 1);
  await page.retryMore(); assert.equal(page.data.customers.length, 51);
  const empty = pageFor({ listCustomerClaimPool: async () => directory([]) }).page;
  await empty.loadCustomers(); assert.equal(empty.data.total, 0); assert.equal(empty.data.loadError, '');
});

test('搜索输入即失效旧响应，防抖期间不出现旧数据；只请求最后关键词第一页', async () => {
  const old = deferred(), calls = [];
  const { page, runSearch } = pageFor({ listCustomerClaimPool: options => {
    calls.push({ ...options }); return options.q ? Promise.resolve(directory([customer(options.q)])) : old.promise;
  } });
  const first = page.loadCustomers();
  page.inputQuery({ detail: { value: '甲' } });
  page.inputQuery({ detail: { value: '  乙  ' } });
  old.resolve(directory([customer('old')])); await first;
  assert.equal(page.data.customers.length, 0); assert.equal(page.data.total, null);
  assert.equal(page.data.query, '乙'); assert.equal(calls.length, 1);
  runSearch(); await tick();
  assert.deepEqual(calls.at(-1), { q: '乙', pageSize: 50, offset: 0 });
  assert.equal(page.data.customers[0].id, '乙');
});

test('旧追加响应不能混入搜索结果；显式刷新从第一页重建并清除选择', async () => {
  const late = deferred(), calls = [];
  const { page, runSearch } = pageFor({ listCustomerClaimPool: options => {
    calls.push({ ...options });
    if (options.q) return Promise.resolve(directory([customer('new')]));
    return options.offset ? late.promise : Promise.resolve(directory(Array.from({ length: 50 }, (_, i) => customer(i)), 51));
  } });
  await page.loadCustomers(); const more = page.loadMore();
  page.inputQuery({ detail: { value: 'new' } }); runSearch(); await tick();
  late.resolve(directory([customer(50)], 51, 50)); await more;
  assert.equal(page.data.customers.length, 1); assert.equal(page.data.total, 1);
  page.selectCustomer({ currentTarget: { dataset: { id: 'new' } } });
  await page.refreshCustomers();
  assert.equal(page.data.selectedCustomerId, ''); assert.equal(calls.at(-1).offset, 0);
});

for (const change of ['account', 'login', 'permissions', 'role', 'teams']) {
  test(`${change}变化丢弃旧页，onShow重新查目录，旧失败不覆盖新数据`, async () => {
    const old = deferred(); let count = 0;
    const { page, app } = pageFor({ listCustomerClaimPool: () => ++count === 1 ? old.promise : Promise.resolve(directory([customer('current')])) });
    const first = page.loadCustomers();
    if (change === 'account') app.globalData.session.userId = 'other';
    if (change === 'login') app.globalData.session.loginAt = 2;
    if (change === 'permissions') app.globalData.session.permissionVersion = 'v2';
    if (change === 'role') app.globalData.role = 'supervisor';
    if (change === 'teams') app.globalData.session.teamIds = ['other'];
    page.onShow(); await tick();
    old.reject(new Error('旧请求失败')); await first;
    assert.equal(page.data.customers[0].id, 'current'); assert.equal(page.data.loadError, '');
    assert.equal(count, 2);
  });
}

test('缺少总数不得以页长补数；重复客户或跨页总量变化提示刷新', async () => {
  const missing = pageFor({ listCustomerClaimPool: async () => ({ items: [customer(1)] }) }).page;
  await missing.loadCustomers(); assert.equal(missing.data.total, null); assert.match(missing.data.loadError, /不完整/);
  for (const changedTotal of [false, true]) {
    const { page } = pageFor({ listCustomerClaimPool: async ({ offset }) => offset
      ? directory([customer(changedTotal ? 50 : 0)], changedTotal ? 52 : 51, 50)
      : directory(Array.from({ length: 50 }, (_, i) => customer(i)), 51) });
    await page.loadCustomers(); await page.loadMore();
    assert.equal(page.data.customers.length, 50); assert.equal(page.data.total, 51);
    assert.equal(page.data.refreshRequired, true); assert.match(page.data.loadMoreError, /名单已变化/);
    await page.retryMore(); assert.equal(page.data.nextOffset, 50); assert.equal(page.data.refreshRequired, false);
  }
});

test('申请成功仅展示待审批，清除分页重取当前搜索；重复点击只写一次', async () => {
  let claims = 0; const queries = [];
  const { page, toasts, store } = pageFor({
    listCustomerClaimPool: async options => { queries.push({ ...options }); return directory([
      { ...customer('c'), claim_status: claims ? 'pending' : null }]); },
    claimCustomer: async id => { claims++; assert.equal(id, 'c'); return { status: 'pending', claimed: false }; },
  });
  await page.loadCustomers('客户');
  page.selectCustomer({ currentTarget: { dataset: { id: 'c' } } });
  page.confirmClaim(); page.confirmClaim(); await tick();
  assert.equal(claims, 1); assert.match(page.data.resultMessage, /等待运营审批/);
  assert.equal(page.data.customers[0].claimEligible, false); assert.equal(page.data.selectedCustomerId, '');
  assert.deepEqual(queries.at(-1), { q: '客户', offset: 0, pageSize: 50 });
  assert.equal(store.has('pendingBattleCustomerId'), false); assert.ok(toasts.includes('申请已提交'));
});

test('申请回调和确认弹窗均不能越过换号；卸载后的读取不更新页面', async () => {
  const claim = deferred(); let calls = 0;
  const { page, app, wx, toasts } = pageFor({ listCustomerClaimPool: async () => directory([customer('c')]),
    claimCustomer: () => { calls++; return claim.promise; } });
  await page.loadCustomers(); page.selectCustomer({ currentTarget: { dataset: { id: 'c' } } });
  let modal; wx.showModal = options => { modal = options; };
  page.confirmClaim(); app.globalData.session.loginAt = 2; modal.success({ confirm: true });
  assert.equal(calls, 0);
  page.onShow(); await tick(); page.selectCustomer({ currentTarget: { dataset: { id: 'c' } } });
  wx.showModal = options => options.success({ confirm: true }); page.confirmClaim();
  app.globalData.session.userId = 'other'; page.onShow(); await tick();
  claim.resolve({ status: 'pending' }); await tick();
  assert.equal(page.data.resultMessage, ''); assert.equal(toasts.includes('申请已提交'), false);
  const read = deferred(); const unloaded = pageFor({ listCustomerClaimPool: () => read.promise }).page;
  const loading = unloaded.loadCustomers(); unloaded.onUnload(); read.resolve(directory([customer('late')])); await loading;
  assert.equal(unloaded.data.customers.length, 0);
});

test('后端提供行业与状态选项，组合筛选翻页保留条件，切换立即清选择且旧页不串入', async () => {
  const calls = [], late = deferred();
  const { page } = pageFor({ listCustomerClaimPool: async opts => {
    calls.push({ ...opts });
    if (opts.offset) return late.promise;
    return opts.claimStatus ? directory([customer('filtered')])
      : directory(Array.from({length: 50}, (_, i) => customer(i)), 51);
  } });
  await page.loadCustomers('st');
  assert.equal(page.data.industryOptions[1].label, '软件');
  await page.changeIndustry({detail: {value: '1'}});
  page.selectCustomer({currentTarget: {dataset: {id: '0'}}});
  const more = page.loadMore();
  assert.equal(calls.at(-1).industry, '软件'); assert.equal(calls.at(-1).offset, 50);
  await page.changeClaimStatus({detail: {value: '2'}});
  assert.equal(page.data.selectedCustomerId, '');
  assert.deepEqual(calls.at(-1), {q: 'st', pageSize: 50, offset: 0, industry: '软件', claimStatus: 'pending'});
  late.resolve(directory([customer('late')], 51, 50)); await more;
  assert.deepEqual(Array.from(page.data.customers, x => x.id), ['filtered']);
  await page.clearFilters();
  assert.deepEqual(calls.at(-1), {q: '', pageSize: 50, offset: 0});
  assert.equal(page.data.hasFilters, false);
});

test('筛选选项失败可重试，不编造选项；缺失选项响应显式报错', async () => {
  let attempts = 0;
  const { page } = pageFor({ listCustomerClaimOptions: async () => {
    if (++attempts === 1) throw new Error('筛选暂时不可用');
    return attempts === 2 ? {industries: [], claim_statuses: []} : filterOptions();
  }, listCustomerClaimPool: async () => directory([]) });
  await page.loadCustomers();
  assert.equal(page.data.industryOptions.length, 0); assert.match(page.data.optionsError, /暂时不可用/);
  assert.equal(page.data.total, 0);
  await page.retryFilterOptions(); assert.match(page.data.optionsError, /不完整/);
  await page.retryFilterOptions(); assert.equal(page.data.optionsError, '');
  assert.equal(page.data.industryOptions.length, 3);
});

test('换账号清空筛选和旧选项，旧选项响应不能带回原公司数据', async () => {
  const late = deferred(), calls = []; let count = 0;
  const {page, app} = pageFor({listCustomerClaimOptions: () => ++count === 2 ? late.promise : Promise.resolve(filterOptions()),
    listCustomerClaimPool: async opts => {calls.push({...opts}); return directory([]);} });
  await page.loadCustomers(); await page.changeIndustry({detail: {value: 1}});
  const refresh = page.refreshCustomers();
  app.globalData.session.userId = 'other'; await page.onShow();
  assert.equal(page.data.industry, ''); assert.equal(page.data.query, '');
  const foreign = filterOptions(); foreign.industries.push({value: '旧公司行业', label: '旧公司行业'});
  late.resolve(foreign); await refresh;
  assert.equal(page.data.industryOptions.some(x => x.label === '旧公司行业'), false);
  assert.deepEqual(calls.at(-1), {q: '', pageSize: 50, offset: 0});
});

test('行业被移除后刷新同时移除旧筛选，不能显示全部行业却暗中带条件', async () => {
  let removed = false; const calls = [];
  const {page} = pageFor({listCustomerClaimOptions: async () => {
    const options = filterOptions(); if (removed) options.industries.splice(1, 1); return options;
  }, listCustomerClaimPool: async opts => {calls.push({...opts}); return directory([]);} });
  await page.loadCustomers(); await page.changeIndustry({detail: {value: 1}});
  removed = true; await page.refreshCustomers();
  assert.equal(page.data.industry, ''); assert.equal(page.data.industryIndex, 0);
  assert.equal(page.data.hasFilters, false); assert.equal(calls.at(-1).industry, undefined);
});
