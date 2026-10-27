const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { normalizeKnowledgeOverview, knowledgeOverviewError } = require('../miniprogram/utils/knowledgeOverview');

const SOURCE='salesbuddy.internal_materials';
const stats=count=>({source:SOURCE,observed_at:'2026-10-18T00:00:00Z',metrics:{total:{status:'available',count,source:SOURCE,observed_at:'2026-10-18T00:00:00Z'}}});
test('home uses the complete authorized system material total, not the customer-facing catalogue page',()=>{
  assert.equal(normalizeKnowledgeOverview(stats(114)).count,114);
  assert.equal(normalizeKnowledgeOverview(stats(114)).sourceLabel,'系统资料');
  assert.equal(normalizeKnowledgeOverview({source:'salesbuddy.customer_distribution',items:[],total:114}).count,null);
});
test('reliable zero is available; malformed or unknown remains a dash',()=>{
  assert.equal(normalizeKnowledgeOverview(stats(0)).value,'0');
  for(const input of [null,{},...[-1,1.5,'12',null,NaN,Infinity].map(stats),{...stats(2),metrics:null},{...stats(2),source:'other'}])assert.equal(normalizeKnowledgeOverview(input).value,'—');
  assert.equal(knowledgeOverviewError('timeout').value,'—');
});
function home() {
  const filename = path.resolve(__dirname, '../miniprogram/pages/index/index.js');
  const requests = [], navigation = [], notices = [];
  const app = {globalData: {role: 'sales', session: {workspaceId: 'company-a', userId: 'reader',
    role: 'sales', scope: '本人', teamIds: [], loginAt: 1, permissionVersion: 'v1'}}};
  const api = {getKnowledgeStats: () => new Promise((resolve, reject) => requests.push({args:[{path:'/knowledge/stats'}], resolve, reject}))};
  let page;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => {page = definition;}, getApp: () => app,
    require: name => name.endsWith('/apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    wx: {navigateTo: options => navigation.push(options), showToast: options => notices.push(options)}, console, setTimeout, clearTimeout, setInterval, clearInterval,
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  return {page, app, api, requests, navigation, notices};
}

test('disabled knowledge entry has no stats prefetch and a stale tap only shows unavailable', () => {
  const {page, requests, navigation, notices} = home();
  assert.equal(page.loadKnowledgeOverview, undefined);
  assert.equal(page.data.knowledgeOverview, undefined);
  page.openKnowledgeLibrary();
  assert.equal(notices[0].title, '知识库暂不能使用');
  assert.equal(requests.length, 0);
  assert.equal(navigation.length, 0);
  const markup = fs.readFileSync(path.resolve(__dirname, '../miniprogram/pages/index/index.wxml'), 'utf8');
  assert.match(markup, /aria-disabled="true" aria-label="知识库，暂不能使用"/);
  assert.doesNotMatch(markup, /bindtap="openKnowledgeLibrary"/);
});

test('both direct Mini knowledge routes are static and cannot load files or external links', () => {
  for (const name of ['knowledge', 'knowledge-webview']) {
    const root = path.resolve(__dirname, '../miniprogram/pages', name);
    let page;
    vm.runInNewContext(fs.readFileSync(path.join(root, 'index.js'), 'utf8'), {
      Page: value => {page = value;}, require: () => {throw Error('No knowledge adapter should load');},
      wx: new Proxy({}, {get: () => {throw Error('No knowledge platform action should run');}}),
    });
    assert.deepEqual(Object.keys(page), ['data']);
    const markup = fs.readFileSync(path.join(root, 'index.wxml'), 'utf8');
    assert.match(markup, /暂不能使用/);
    assert.doesNotMatch(markup, /web-view|button|input|bindtap|catchtap/);
  }
});
