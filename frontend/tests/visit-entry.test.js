const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makePage(api = {}) {
  let definition;
  const storage = new Map();
  const wx = {setStorageSync: (key, value) => storage.set(key, value)};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.js', 'utf8'), {
    require: name => name.includes("apiClient") ? api : ({}), Page: value => { definition = value; }, wx, clearTimeout, clearInterval,
  });
  return { ...definition, draftKey: 'visit-test', storage, data: {...definition.data},
    setData(value) { Object.assign(this.data, value); } };
}

test('切换录入方式保留已输入文字和已提取文件，不重复提取', () => {
  const page = makePage();
  Object.assign(page.data, {transcript:'已核对的拜访内容', importId:'import-1', fileName:'拜访.md',
    importStatus:'succeeded', appliedImportId:'import-1', inputHelpVisible:true});
  page.switchInputMode({currentTarget:{dataset:{mode:'file'}}});
  assert.equal(page.data.entryMode, 'file');
  assert.equal(page.data.inputHelpVisible, false);
  assert.equal(page.data.transcript, '已核对的拜访内容');
  assert.equal(page.storage.get('visit-test').appliedImportId, 'import-1');
  page.switchInputMode({currentTarget:{dataset:{mode:'voice'}}});
  assert.equal(page.data.fileName, '拜访.md');
  assert.equal(page.data.transcript, '已核对的拜访内容');
});

test('录音启动、录制、结束及处理期间锁定模式，未初始化录音器可退出', () => {
  for (const busyState of ['isStarting', 'isRecording', 'isStopping', 'isProcessing']) {
    const page = makePage();
    page.data[busyState] = true;
    page.switchInputMode({currentTarget:{dataset:{mode:'file'}}});
    assert.equal(page.data.entryMode, 'voice', busyState);
  }
  assert.doesNotThrow(() => makePage().onUnload());
});

test('首次拜访选择随录入草稿保存', () => {
  const page = makePage();
  page.toggleFirstVisit({detail:{value:['first']}});
  assert.equal(page.data.isFirstVisit, true);
  assert.equal(page.storage.get('visit-test').isFirstVisit, true);
  page.toggleFirstVisit({detail:{value:[]}});
  assert.equal(page.data.isFirstVisit, false);
});

test('文件录入入口使用明确的文件类型说明', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxml', 'utf8');
  assert.match(wxml, /支持音频与文档文件/);
  assert.doesNotMatch(wxml, /录音与文档都可以/);
});

test('结构化提交按钮只保留文字，不显示箭头', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxml', 'utf8');
  const wxss = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxss', 'utf8');
  const submit = wxml.match(/<button class="submit-button[\s\S]*?<\/button>/)?.[0] || '';
  assert.doesNotMatch(submit, /submit-arrow|→/);
  assert.doesNotMatch(wxss, /submit-arrow/);
});

test('首次拜访整合在拜访原始记录卡片内', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxml', 'utf8');
  const note = wxml.match(/<view class="note-card">[\s\S]*?<\/view>\n\s*<\/view>\n\s*<view class="capture-card/)?.[0] || '';
  assert.match(note, /class="first-visit-field"/);
  assert.match(note, />首次拜访</);
  assert.doesNotMatch(wxml, /class="first-visit-card"/);
});

test('拜访录入先从公司客户中搜索并确认客户', () => {
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.js', 'utf8');
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxml', 'utf8');
  assert.match(wxml, /输入客户名称关键词/);
  assert.match(wxml, /class="customer-results"/);
  assert.match(wxml, /!customerConfirmed \? '请先选择客户'/);
  assert.match(js, /listCustomers\(\{ q: query, scope: isFde \? "mine" : "company", pageSize: 100 \}\)/);
  assert.match(js, /if \(!this\.data\.customerId\)[\s\S]*请先选择客户/);
});


test('带入客户按 ID 回读数据库名称，过期关联不能继续提交', async () => {
  const page = makePage({getCustomerReference: async id => ({id, name:'数据库客户名称'})});
  page.data.transcript = '待提交的原文';
  await page.loadLinkedCustomer('c1');
  assert.equal(page.data.customerName, '数据库客户名称');
  assert.equal(page.data.customerConfirmed, true);
  assert.equal(page.data.canSubmit, true);
  const forbidden = makePage({getCustomerReference: async () => {throw Error('客户不存在或不可见')}});
  await forbidden.loadLinkedCustomer('removed');
  assert.equal(forbidden.data.customerId, '');
  assert.equal(forbidden.data.canSubmit, false);
  assert.match(forbidden.data.errorText, /不可见/);
});

test('旧客户请求晚到不能覆盖用户刚选的客户', async () => {
  let resolve;
  const page = makePage({getCustomerReference: () => new Promise(r => {resolve=r})});
  const pending = page.loadLinkedCustomer('old');
  page.confirmSelectedCustomer({id:'new',name:'新选择'});
  resolve({id:'old',name:'旧客户'});
  await pending;
  assert.equal(page.data.customerId, 'new');
});
