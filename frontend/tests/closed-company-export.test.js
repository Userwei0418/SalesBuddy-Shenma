const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../backend/src/sales_backend/web');
const script = fs.readFileSync(path.join(root, 'assets/closed-company-export.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'closed-company-export.html'), 'utf8');
const subject = import('data:text/javascript;base64,' + Buffer.from(script).toString('base64'));
const tick = () => new Promise(setImmediate);
const initialTime = Date.parse('2026-09-27T12:00:00Z');
const identity = {workspace_id:'company-a-id', company_name:'关闭公司甲', company_code:'company-a', account_code:'EXPORTADMIN', display_name:'合成管理员', expires_at:'2026-09-27T12:15:00Z'};
const options = {sections:[{code:'customers', label:'客户', available:true}, {code:'visits', label:'跟进记录', available:true}, {code:'targets', label:'目标', available:false}], originals_available:true, max_bytes:1024 * 1024, max_records:100, note:'本公司记录'};

async function setup() {
  const requests = [], nodes = new Map(), timers = new Map(), clicks = [], revoked = [], created = [];
  let currentTime = initialTime, timerId = 0;
  function element(tag = 'DIV') {
    return {tagName:tag, children:[], listeners:{}, textContent:'', value:'', checked:false, disabled:false, hidden:false,
      classList:{toggle() {}}, addEventListener(type, fn) { this.listeners[type] = fn; },
      append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; },
      click() { clicks.push(this); }, remove() { this.removed = true; },
      querySelectorAll(selector) { assert.equal(selector, 'input:checked'); return this.children.flatMap(child => child.children).filter(child => child.tagName === 'INPUT' && child.checked); },
      reset() { this.fields = {}; this.resetCount = (this.resetCount || 0) + 1; }};
  }
  for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], element(match[1].endsWith('-form') ? 'FORM' : 'DIV'));
  const document = {body:element('BODY'), listeners:{}, getElementById(id) { assert(nodes.has(id), 'unknown element ' + id); return nodes.get(id); }, createElement:tag => element(tag.toUpperCase()), addEventListener(type, fn) { this.listeners[type] = fn; }};
  class Fields {
    constructor(form) { this.values = {...form.fields}; }
    get(name) { return this.values[name] ?? null; }
    getAll(name) { return this.values[name] || []; }
    has(name) { return Object.hasOwn(this.values, name); }
  }
  const page = (await subject).createClosedCompanyExportPage({document, FormData:Fields,
    fetch:(url, config) => new Promise((resolve, reject) => requests.push({url, config, resolve, reject})),
    URL:{createObjectURL(blob) { created.push(blob); return 'blob:synthetic-' + created.length; }, revokeObjectURL:url => revoked.push(url)},
    setTimeout:(fn, delay) => { const id = ++timerId; timers.set(id, {fn, delay}); return id; }, clearTimeout:id => timers.delete(id), now:() => currentTime});
  const respond = (request, status, data = null) => request.resolve({status, ok:status >= 200 && status < 300, json:async () => data});
  const respondZip = (request, {length = 4, size = 4, mime = 'application/zip', blob = async () => ({size})} = {}) => request.resolve({status:200, ok:true, headers:{get:name => ({'Content-Length':String(length), 'Content-Type':mime})[name]}, blob});
  const event = {preventDefault() {}};
  const fire = (id, type = 'click') => nodes.get(id).listeners[type](event);
  const start = page.start();
  const loggedIn = async (value = identity, choices = options) => {
    respond(requests[0], 200, value); await tick();
    respond(requests.at(-1), 200, choices); await start;
  };
  const exportData = (fields = {sections:['customers','visits'], reason:'历史资料归档'}) => { nodes.get('export-form').fields = fields; return fire('export-form', 'submit'); };
  return {nodes, document, requests, timers, clicks, revoked, created, respond, respondZip, fire, start, loggedIn, exportData, advance:ms => { currentTime += ms; }};
}

test('dedicated page uses existing logo and no ordinary business shell or token storage', () => {
  assert.match(html, /\/admin\/assets\/brand-white\.svg/);
  assert.doesNotMatch(html, /console\.js|platform\.js|data-nav|sidebar/);
  assert.doesNotMatch(script, /localStorage|sessionStorage|document\.cookie|Authorization|\/refresh/);
  assert.match(fs.readFileSync(path.join(root, 'assets/console.js'), 'utf8'), /href="\/closed-company-export"/);
});

test('existing session obtains only dedicated options, exact expiry and permission-filtered choices', async () => {
  const x = await setup(); await x.loggedIn();
  assert.deepEqual(x.requests.map(r => r.url), ['/api/v1/closed-company-export/session', '/api/v1/closed-company-export/options']);
  for (const request of x.requests) { assert.equal(request.config.credentials, 'same-origin'); assert.equal(request.config.cache, 'no-store'); }
  assert.equal(x.nodes.get('company-name').textContent, identity.company_name);
  assert.equal(x.nodes.get('login-panel').hidden, true);
  assert.equal(x.nodes.get('export-form').hidden, false);
  const inputs = x.nodes.get('sections').children.map(label => label.children[0]);
  assert.deepEqual(inputs.map(input => [input.value, input.checked, input.disabled]), [['customers',true,false], ['visits',true,false], ['targets',false,true]]);
  assert.equal([...x.timers.values()][0].delay, 15 * 60000);
  assert.match(x.nodes.get('capacity').textContent, /1 MiB、100 条/);
});

test('login sends explicit company plus original password once and clears password input immediately', async () => {
  const x = await setup(); x.respond(x.requests[0], 401); await x.start;
  x.nodes.get('login-form').fields = {company_code:' company-a ', account_code:' 13900000000 ', password:' synthetic password '};
  x.nodes.get('login-password').value = ' synthetic password ';
  const action = x.fire('login-form', 'submit');
  assert.equal(x.nodes.get('login-password').value, '');
  assert.deepEqual(JSON.parse(x.requests[1].config.body), {company_code:'company-a', account_code:'13900000000', password:' synthetic password '});
  x.respond(x.requests[1], 200, identity); await tick(); x.respond(x.requests[2], 200, options); await action;
  assert.equal(x.nodes.get('export-panel').hidden, false);
});

test('403 preserves current company and offers an options retry without switching identity', async () => {
  const x = await setup(); x.respond(x.requests[0], 200, identity); await tick();
  x.respond(x.requests[1], 403, {detail:'当前账号未获资料导出权限'}); await x.start;
  assert.equal(x.nodes.get('company-name').textContent, identity.company_name);
  assert.equal(x.nodes.get('login-panel').hidden, true);
  assert.equal(x.nodes.get('retry-options').hidden, false);
  assert.match(x.nodes.get('notice').textContent, /未获资料导出权限/);
  assert.equal(x.requests.length, 2);
});

test('invalid options fail closed instead of enabling an incomplete export', async () => {
  const x = await setup(); await x.loggedIn(identity, {...options, max_bytes:undefined});
  assert.equal(x.nodes.get('export-form').hidden, true);
  assert.equal(x.nodes.get('retry-options').hidden, false);
  assert.match(x.nodes.get('notice').textContent, /选项不完整/);
});

test('originals follow visits selection and cannot bypass missing original permission', async () => {
  const x = await setup(); await x.loggedIn();
  const visit = x.nodes.get('sections').children[1].children[0];
  x.nodes.get('include-originals').checked = true; visit.checked = false; visit.listeners.change();
  assert.equal(x.nodes.get('include-originals').disabled, true);
  assert.equal(x.nodes.get('include-originals').checked, false);
  await x.exportData({sections:['customers'], include_originals:'on', reason:'历史资料归档'});
  assert.equal(x.requests.length, 2);
  assert.match(x.nodes.get('notice').textContent, /同时选择跟进记录/);
  const y = await setup(); await y.loggedIn(identity, {...options, originals_available:false});
  assert.equal(y.nodes.get('include-originals').disabled, true);
});

test('export requires a category and useful reason before calling server', async () => {
  const x = await setup(); await x.loggedIn();
  await x.exportData({sections:[], reason:'历史资料归档'});
  assert.match(x.nodes.get('notice').textContent, /至少一个/);
  await x.exportData({sections:['customers'], reason:'  a  '});
  assert.match(x.nodes.get('notice').textContent, /3–200/);
  assert.equal(x.requests.length, 2);
});

test('complete ZIP creates a download only after receiving body and never claims delivery', async () => {
  const x = await setup(); await x.loggedIn();
  let finish; const action = x.exportData({sections:['customers','visits'], include_originals:'on', reason:' 历史资料归档 '});
  const request = x.requests.at(-1);
  assert.deepEqual(JSON.parse(request.config.body), {sections:['customers','visits'], include_originals:true, reason:'历史资料归档'});
  x.respondZip(request, {blob:() => new Promise(resolve => { finish = resolve; })}); await tick();
  assert.equal(x.clicks.length, 0);
  finish({size:4}); await action;
  assert.equal(x.clicks.length, 1); assert.match(x.clicks[0].download, /company-a.*\.zip$/);
  assert.match(x.nodes.get('notice').textContent, /无法确认文件是否已保存/);
  assert.equal(x.nodes.get('export-fields').disabled, false);
});

for (const [label, response] of [['wrong media type',{mime:'application/json'}], ['oversize',{length:2 * 1024 * 1024}], ['truncated body',{size:2}]]) {
  test('rejects ' + label + ' without saving a misleading ZIP', async () => {
    const x = await setup(); await x.loggedIn(); const action = x.exportData();
    x.respondZip(x.requests.at(-1), response); await action;
    assert.equal(x.clicks.length, 0); assert.match(x.nodes.get('notice').textContent, /不完整|超出容量/);
  });
}

test('403 export keeps identity and selection while 401 clears all company data', async () => {
  const x = await setup(); await x.loggedIn(); let action = x.exportData();
  x.respond(x.requests.at(-1), 403, {detail:'权限已收回'}); await action;
  assert.equal(x.nodes.get('company-name').textContent, identity.company_name);
  assert.equal(x.nodes.get('export-form').fields.reason, '历史资料归档');
  action = x.exportData(); x.respond(x.requests.at(-1), 401); await action;
  assert.equal(x.nodes.get('company-name').textContent, '');
  assert.equal(x.nodes.get('sections').children.length, 0);
  assert.equal(x.nodes.get('export-panel').hidden, true);
  assert.equal(x.nodes.get('login-panel').hidden, false);
});

test('expiry aborts pending download and a late blob cannot reappear or trigger save', async () => {
  const x = await setup(); await x.loggedIn(); let finish;
  const action = x.exportData(), request = x.requests.at(-1);
  x.respondZip(request, {blob:() => new Promise(resolve => { finish = resolve; })}); await tick();
  x.advance(15 * 60000); x.document.listeners.visibilitychange();
  assert.equal(request.config.signal.aborted, true);
  finish({size:4}); await action;
  assert.equal(x.clicks.length, 0); assert.equal(x.nodes.get('company-name').textContent, '');
  assert.match(x.nodes.get('notice').textContent, /已到期/);
  assert.equal(x.requests.length, 3);
});

test('logout discards late options, blocks overlapping login and uses dedicated logout', async () => {
  const x = await setup(); x.respond(x.requests[0], 200, identity); await tick(); const oldOptions = x.requests[1];
  const logout = x.fire('logout');
  assert.equal(x.nodes.get('login-submit').disabled, true);
  await x.fire('login-form', 'submit');
  assert.equal(x.requests.length, 3);
  assert.equal(x.requests[2].url, '/api/v1/closed-company-export/logout');
  assert.equal(x.requests[2].config.method, 'POST');
  x.respond(oldOptions, 200, options); await x.start;
  assert.equal(x.nodes.get('sections').children.length, 0);
  x.respond(x.requests[2], 204); await logout;
  assert.equal(x.nodes.get('login-submit').disabled, false);
  assert.match(x.nodes.get('notice').textContent, /已退出/);
});

test('network errors remain retryable and duplicate export clicks send only one request', async () => {
  const x = await setup(); await x.loggedIn(); const action = x.exportData(); await x.exportData();
  assert.equal(x.requests.length, 3); x.requests[2].reject(new TypeError('Failed to fetch')); await action;
  assert.match(x.nodes.get('notice').textContent, /网络连接中断/);
  assert.equal(x.nodes.get('export-fields').disabled, false);
  assert.equal(x.nodes.get('company-name').textContent, identity.company_name);
});

test('failed logout offers retry and never reports an unconfirmed logout as successful', async () => {
  const x = await setup(); await x.loggedIn(); let logout = x.fire('logout');
  x.requests.at(-1).reject(new TypeError('Failed to fetch')); await logout;
  assert.equal(x.nodes.get('logout').hidden, false);
  assert.equal(x.nodes.get('logout').textContent, '重试退出');
  assert.match(x.nodes.get('notice').textContent, /服务端退出尚未确认/);
  assert.equal(x.nodes.get('company-name').textContent, '');
  logout = x.fire('logout'); x.respond(x.requests.at(-1), 204); await logout;
  assert.equal(x.nodes.get('logout').hidden, true);
  assert.match(x.nodes.get('notice').textContent, /已退出/);
});

test('unresponsive login is bounded and does not retain entered password', async () => {
  const x = await setup(); x.respond(x.requests[0], 401); await x.start;
  assert.match(x.nodes.get('notice').textContent, /请使用获授权/);
  x.nodes.get('login-form').fields = {company_code:'company-a', account_code:'EXPORTADMIN', password:'synthetic'};
  const action = x.fire('login-form', 'submit'), request = x.requests.at(-1);
  const timeout = [...x.timers.values()].find(timer => timer.delay === 30000); timeout.fn();
  assert.equal(request.config.signal.aborted, true);
  request.reject(Object.assign(new Error('aborted'), {name:'AbortError'})); await action;
  assert.match(x.nodes.get('notice').textContent, /请求超时/);
  assert.equal(x.nodes.get('login-password').value, '');
  assert.equal(x.nodes.get('login-submit').disabled, false);
});

test('a late options retry cannot overwrite newer permission choices', async () => {
  const x = await setup(); await x.loggedIn();
  const first = x.fire('retry-options'), old = x.requests.at(-1);
  const second = x.fire('retry-options'), fresh = x.requests.at(-1);
  x.respond(fresh, 200, {...options, originals_available:false}); await second;
  x.respond(old, 200, options); await first;
  assert.equal(x.nodes.get('include-originals').disabled, true);
  assert.match(x.nodes.get('originals-note').textContent, /没有导出原件的权限/);
});
