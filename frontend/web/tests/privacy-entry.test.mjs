import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const platformCode = await readFile(new URL('../browser-platform.js', import.meta.url), 'utf8');
const loginCode = await readFile(new URL('../source/miniprogram/pages/login/index.js', import.meta.url), 'utf8');
const runtimeCode = await readFile(new URL('../runtime.js', import.meta.url), 'utf8');
const shellCode = await readFile(new URL('../shell.js', import.meta.url), 'utf8');
// Exercise the actual host modal without starting a browser or a local server.
const modalCode = runtimeCode.slice(runtimeCode.indexOf('  function modal('), runtimeCode.indexOf('  function ownerElement('));
const fixtureNotice = {schemaVersion: 1, revision: 'synthetic-test-only', title: '测试说明', text: '仅供自动检查的合成文字\n<img src=x onerror=alert(1)>'};

function fixture(notice) {
  const listeners = new Map(), toasts = [], activeModals = new Set();
  const document = {activeElement: null, addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key)};
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.className = ''; this.textContent = ''; this.classList = {add: value => {this.className += ' ' + value;}}; }
    set innerHTML(_) { throw Error('Notice must not render HTML'); }
    setAttribute(key, value) { this.attributes[key] = value; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    get lastChild() { return this.children.at(-1); }
    get isConnected() { return this === root || Boolean(this.parent?.isConnected); }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
    querySelectorAll() { return this.children.flatMap(child => [...(['button', 'input', 'textarea', 'select'].includes(child.tagName) || child.tagName === 'a' && child.href || child.tabIndex === 0 ? [child] : []), ...child.querySelectorAll()]); }
  }
  document.createElement = tag => new Element(tag);
  const root = new Element('body'), trigger = new Element('button'), overlays = new Element('div'); root.append(trigger, overlays); trigger.focus();
  const window = {SALES_MODE: 'live', SALES_BUNDLE: {privacyNotice: notice}, location: {href: 'https://sales.example.test/web/?mode=live#/pages/login/index', origin: 'https://sales.example.test'}};
  const context = vm.createContext({window, URL, document, overlays, activeModals, requestAnimationFrame: callback => callback(), report: error => {throw error;}, current: null});
  vm.runInContext(modalCode + ';globalThis.testModal = modal;', context);
  vm.runInContext(platformCode, context);
  const wx = {showModal: context.testModal, showToast: item => toasts.push(item.title)};
  window.SalesPlatform.install(wx);
  let page;
  Object.assign(context, {wx, require: name => name.endsWith('apiClient') ? {getBaseUrl: () => '/api/v1'} : name.endsWith('rememberedLogin') ? {normalizeAccount: x => x.toUpperCase()} : {}, Page: value => {page = value;}, getApp: () => {throw Error('Privacy must not login or mutate the app');}});
  vm.runInContext(loginCode, context);
  page.data = {...page.data, account: 'synthetic_user', password: 'synthetic_password'};
  page.setData = value => Object.assign(page.data, value); context.current = page;
  return {page, wx, document, overlays, trigger, toasts, activeModals, listeners, window, context};
}

test('missing or incomplete formal notice fails without displaying substitute terms or changing consent', () => {
  for (const notice of [null, {}, {...fixtureNotice, revision: ''}, {...fixtureNotice, text: ''}]) {
    const f = fixture(notice); f.page.openPrivacy();
    assert.equal(f.overlays.children.length, 0); assert.equal(f.page.data.agreed, false);
    assert.deepEqual(f.toasts, ['隐私指引暂不可用，请联系运营']);
  }
});

test('shared login reader opens literal text, closes with focus restored, and still requires explicit consent', async () => {
  const f = fixture(fixtureNotice), before = JSON.stringify(f.page.data);
  f.page.openPrivacy();
  const mask = f.overlays.lastChild, box = mask.lastChild, [heading, content, buttons] = box.children;
  assert.equal(mask.attributes['aria-label'], fixtureNotice.title); assert.equal(heading.textContent, fixtureNotice.title);
  assert.equal(content.textContent, fixtureNotice.text); assert.equal(content.children.length, 0);
  assert.match(box.className, /wx-privacy-notice/); assert.equal(content.tabIndex, 0);
  assert.equal(buttons.children.length, 1); assert.equal(buttons.lastChild.textContent, '关闭');
  buttons.lastChild.onclick();
  assert.equal(f.overlays.children.length, 0); assert.equal(f.document.activeElement, f.trigger);
  assert.equal(JSON.stringify(f.page.data), before, 'Reading cannot change credentials or consent');
  await f.page.submitLogin(); assert.deepEqual(f.toasts, ['请先同意隐私与数据使用说明']);
  f.page.toggleAgreement({detail: {checked: true}}); f.page.openPrivacy(); f.overlays.lastChild.lastChild.lastChild.lastChild.onclick();
  assert.equal(f.page.data.agreed, true, 'Reading also preserves an existing explicit choice');
});

test('Escape and page dismissal clean up the reader and invoke callbacks exactly once', async () => {
  for (const routeChange of [false, true]) {
    const f = fixture(fixtureNotice); let successes = 0, failures = 0, completions = 0;
    const done = f.wx.openPrivacyContract({success: () => successes++, fail: () => failures++, complete: () => completions++});
    const record = [...f.activeModals][0];
    if (routeChange) record.finish(false, false, true);
    else f.listeners.get('keydown')({key: 'Escape', preventDefault() {}, stopPropagation() {}});
    record.finish(true); await done;
    assert.equal(successes, routeChange ? 0 : 1); assert.equal(failures, routeChange ? 1 : 0); assert.equal(completions, 1);
    assert.equal(f.listeners.size, 0); assert.equal(f.overlays.children.length, 0); assert.equal(f.page.data.agreed, false);
  }
});

test('confirmed URL is a safe new-window reading link, including same-origin deployment paths', () => {
  for (const [raw, expected] of [['https://policy.example.test/formal', 'https://policy.example.test/formal'], ['./policy.pdf', 'https://sales.example.test/web/policy.pdf']]) {
    const f = fixture({...fixtureNotice, text: '', url: raw}); f.page.openPrivacy();
    const content = f.overlays.lastChild.lastChild.children[1], link = content.lastChild;
    assert.equal(link.href, expected); assert.equal(link.target, '_blank'); assert.equal(link.rel, 'noopener noreferrer');
    assert.equal(content.textContent, ''); assert.equal(f.page.data.agreed, false);
    let prevented = false; f.listeners.get('keydown')({key: 'Tab', preventDefault() {prevented = true;}});
    assert.equal(prevented, true); assert.equal(f.document.activeElement, content, 'Tab cycles through the reader, URL and close control');
  }
});

test('unsafe URLs fail closed before a link or a reader is created', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,example', 'http://elsewhere.test/policy', 'https://user:password@policy.example.test/']) {
    const f = fixture({...fixtureNotice, url}); f.page.openPrivacy();
    assert.equal(f.overlays.children.length, 0); assert.equal(f.page.data.agreed, false); assert.equal(f.toasts.length, 1);
  }
});

test('approved notice keeps its fixed original and opens fully without selecting consent', async () => {
  const bytes = await readFile(new URL('../privacy-notice.json', import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '3a597b1a2f4ea9fbfec529cc67c27d9debdf58e2529c65a5d4e7b38e33764293');
  const notice = JSON.parse(bytes);
  assert.deepEqual(Object.keys(notice).sort(), ['revision', 'schemaVersion', 'text', 'title']);
  assert.equal(notice.schemaVersion, 1); assert.equal(notice.revision, '2026-10-15-shenma');
  assert.equal(notice.title, '神码 SalesBuddy 隐私与数据使用说明');
  assert.equal(notice.text.length, 1898); assert.doesNotMatch(notice.text, /商汤科技|2446796988/); assert.match(notice.text, /企业管理员/);
  const f = fixture(notice); assert.equal(f.page.data.agreed, false); f.page.openPrivacy();
  assert.equal(f.overlays.lastChild.lastChild.children[1].textContent, notice.text.trim());
  assert.equal(f.page.data.agreed, false); f.overlays.lastChild.lastChild.lastChild.lastChild.onclick();
  assert.equal(f.overlays.children.length, 0); assert.equal(f.page.data.agreed, false); assert.equal(f.document.activeElement, f.trigger);
});

test('privacy failures remain visible as a toast without becoming persistent authentication errors', () => {
  const f = fixture(null), loginError = {textContent: '', hidden: true}, events = [];
  f.page.route = 'pages/login/index'; f.document.getElementById = () => loginError;
  let onToast;
  f.window.addEventListener = (_, callback) => {onToast = callback;};
  f.window.dispatchEvent = event => {events.push(event.detail); onToast(event);};
  Object.assign(f.context, {SalesRuntime: {current: f.page}, setTimeout: () => {}, CustomEvent: class {constructor(type, {detail}) {this.type = type; this.detail = detail;}}});
  const toastCode = runtimeCode.slice(runtimeCode.indexOf("  let toastSource = ''"), runtimeCode.indexOf('  function report('));
  vm.runInContext(toastCode + ';globalThis.testToast = toast; globalThis.testWithToastSource = withToastSource;', f.context);
  const listener = shellCode.slice(shellCode.indexOf("  window.addEventListener('sales:toast'"), shellCode.indexOf("  $('page-root').addEventListener('input'"));
  vm.runInContext('const $ = id => document.getElementById(id);' + listener, f.context);
  f.wx.showToast = options => f.context.testToast(options.title);
  f.window.SalesPlatform.install(f.wx, {withToastSource: f.context.testWithToastSource});
  f.page.openPrivacy();
  assert.equal(events.at(-1).source, 'privacy'); assert.equal(f.overlays.lastChild.textContent, '隐私指引暂不可用，请联系运营');
  assert.equal(loginError.hidden, true); assert.equal(loginError.textContent, '');
  f.wx.showToast({title: '账号或密码错误'});
  assert.equal(events.at(-1).source, ''); assert.equal(loginError.hidden, false); assert.equal(loginError.textContent, '账号或密码错误');
  f.page.openPrivacy();
  assert.equal(loginError.textContent, '账号或密码错误', 'A reading error must not replace a real login error');
  assert.throws(() => f.context.testWithToastSource('privacy', () => {throw Error('test callback failure');}), /test callback failure/);
  f.wx.showToast({title: '登录请求超时'});
  assert.equal(events.at(-1).source, ''); assert.equal(loginError.textContent, '登录请求超时');
  assert.equal(f.page.data.agreed, false);
});
