import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../entry.js', import.meta.url), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function boot(url, capability = {ok: false}, autoLoad = true) {
  const scripts = [], preloads = [], panels = [], calls = [], timers = new Map();
  let timerId = 0;
  const document = {
    createElement(tag) { return {tag, children: [], append(...nodes) {this.children.push(...nodes);}, remove() {this.removed = true;}}; },
    head: {append(node) {preloads.push(node);}},
    body: {classList: {add() {}}, append(node) {
      if (node.tag === 'script') {scripts.push(node); if (autoLoad) queueMicrotask(() => node.onload?.());}
      else panels.push(node);
    }},
  };
  const location = new URL(url), window = {}, storage = new Map([['sales-web:preview:v1:salesSession', 'old-demo'], ['sales-web:live:v1:salesSession', 'real-session'], ['sales-web:mode', 'preview']]);
  const done = vm.runInNewContext(source, {document, location, window, URL, Date, AbortSignal,
    fetch: async path => {calls.push(path); if (capability instanceof Error) throw capability; return capability;},
    sessionStorage: {removeItem(key) {storage.delete(key);}}, history: {state: null, replaceState(_state, _unused, href) {location.href = href;}},
    setTimeout: (fn, ms) => {timers.set(++timerId, {fn, ms}); return timerId;}, clearTimeout: id => timers.delete(id),
  });
  return {done, scripts, preloads, panels, calls, timers, window, location, storage};
}

test('static production bypasses local metadata and begins every download before executing dependencies', async () => {
  for (const path of ['/web', '/web/', '/web/index.html']) {
    const state = boot('https://company.example' + path + '?mode=live', undefined, false);
    assert.deepEqual(state.calls, []);
    assert.equal(state.window.SALES_SERVICE.adminUrl, 'https://company.example/admin');
    assert.equal(state.preloads.length, 18);
    assert.ok(state.preloads.every(node => ['preload', 'modulepreload'].includes(node.rel) && node.as === 'script'));
    assert.equal(state.scripts.length, 1);
    for (let i = 0; i < state.preloads.length; i++) {
      assert.equal(state.scripts.length, i + 1, 'later dependencies must not execute early');
      assert.equal(state.scripts[i].src, state.preloads[i].href);
      state.scripts[i].onload(); await tick();
    }
    await state.done;
    assert.equal(state.scripts.at(-1).src, 'shell.js');
    assert.equal(state.scripts.find(node => node.src.startsWith('department-ui/app.js')).type, 'module');
    assert.ok(!state.scripts.some(node => node.src.includes('echarts.min.js')));
    assert.equal(state.panels.length, 0);
    assert.equal(state.timers.size, 0);
  }
});

test('local metadata retains admin navigation while old preview-only services stop before business scripts', async () => {
  const response = body => ({ok: true, headers: {get: () => 'application/json'}, json: async () => body});
  const local = boot('http://127.0.0.1:5188/?mode=live', response({adminUrl: 'http://127.0.0.1:18800/admin'}));
  await local.done;
  assert.deepEqual(local.calls, ['/web-capabilities']);
  assert.equal(local.window.SALES_SERVICE.adminUrl, 'http://127.0.0.1:18800/admin');
  for (const mode of ['live', 'preview']) {
    const stopped = boot('https://preview.example/?mode=' + mode, response({previewOnly: true}));
    await stopped.done;
    assert.equal(stopped.scripts.length, 0);
    assert.equal(stopped.preloads.length, 0);
    assert.equal(stopped.panels.length, 1);
    assert.match(stopped.panels[0].children[0].textContent, /不能用于业务登录/);
    assert.equal(stopped.window.SALES_MODE, 'live');
    assert.equal(stopped.location.search, '?mode=live');
  }
});

test('business entry normalizes every old demo link without restoring or clearing either account store', async () => {
  for (const origin of ['https://company.example/web/', 'http://127.0.0.1:5188/']) {
    for (const query of ['', '?mode=unknown', '?mode=preview', '?mode=live']) {
      const state = boot(origin + query + '#/pages/customer-detail/index?customerId=old-demo');
      await state.done;
      assert.equal(state.window.SALES_MODE, 'live');
      assert.equal(state.location.search, '?mode=live');
      if (query === '?mode=preview') assert.equal(state.location.hash, '#/pages/login/index');
      assert.equal(state.storage.get('sales-web:preview:v1:salesSession'), 'old-demo');
      assert.equal(state.storage.get('sales-web:live:v1:salesSession'), 'real-session');
      assert.equal(state.storage.has('sales-web:mode'), false);
      assert.equal(state.scripts.some(script => /preview|crm-/.test(script.src)), false);
    }
  }
});

test('a missing or unavailable local capability endpoint retains the existing fallback', async () => {
  for (const response of [{ok:false}, new Error('timeout')]) {
    const state = boot('https://legacy.example/?mode=live', response);
    await state.done;
    assert.equal(state.scripts.length, 18);
    assert.equal(state.panels.length, 0);
    assert.equal(state.window.SALES_MODE, 'live');
  }
});

test('failed and stalled downloads stop execution and expose a retry, including late load events', async () => {
  for (const failure of ['error', 'timeout']) {
    const state = boot('https://company.example/web/?mode=live', undefined, false);
    const first = state.scripts[0], lateLoad = first.onload;
    if (failure === 'error') first.onerror();
    else {
      const timer = [...state.timers.values()][0];
      assert.ok(timer.ms > 0 && timer.ms <= 60000);
      timer.fn();
    }
    await state.done;
    lateLoad(); await tick();
    assert.equal(state.scripts.length, 1);
    assert.equal(first.removed, true);
    assert.equal(state.panels.length, 1);
    assert.equal(state.panels[0].children[2].href, 'https://company.example/web/?mode=live');
    assert.doesNotMatch(state.panels[0].children[1].textContent, /command|本地服务/);
    assert.equal(state.timers.size, 0);
  }
});

test('initial loading copy describes assets, before shell starts the independent service check', () => {
  const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.match(html, /id="login-connection-status"[^>]*>正在加载工作空间…</);
});
