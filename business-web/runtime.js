/* Browser host for the delivered Mini Program, with explicit Web session/lifecycle adaptations. */
(() => {
  'use strict';
  const root = document.getElementById('page-root');
  const overlays = document.getElementById('wx-overlays');
  const style = document.getElementById('page-style');
  const errors = window.__runtimeErrors = [];
  let bundle, app, current, stack = [], cache = {}, definitions = {}, componentDefinitions = {};
  let tabPages = new Map(), pendingDeepLink = '', documentBooted = false;
  let frame = 0, rendering = false, serial = 0, titleOverride = '', hiddenTabs = false;
  const sessionKey = name => `sales-web:${name}:${window.SALES_MODE || 'live'}`;
  let navigationEpoch = sessionStorage.getItem(sessionKey('navigation-epoch')) || String(Date.now());
  sessionStorage.setItem(sessionKey('navigation-epoch'), navigationEpoch);
  const authenticated = () => !!(app?.globalData?.session && !app.globalData.session.mustChangePassword);
  function clearSessionNavigation() {
    pendingDeepLink = '';
    sessionStorage.removeItem(sessionKey('pending-path'));
    sessionStorage.setItem(sessionKey('signed-out'), '1');
    navigationEpoch = `${Date.now()}:${++serial}`;
    sessionStorage.setItem(sessionKey('navigation-epoch'), navigationEpoch);
    // These are transient cross-page references, not account-scoped drafts or retry identities.
    for (const key of ['pendingOpenCustomerId', 'pendingOpenOpportunityId', 'pendingBattleCustomerId', 'pendingCustomerContext', 'pendingReportDetail',
      'pendingVisitAudio', 'pendingVisitStructuredDraft', 'pendingVisitTranscript', 'pendingChatBIAudio', 'pendingManagementTaskAudio',
      'lastCompletedTaskId', 'lastResolvedRiskId', 'lastCreatedOpportunity', 'lastCreatedCustomer', 'lastManagementTaskCreated', 'lastManagementCustomerSuccess', 'homeGreetingShownLogin']) wx.removeStorageSync(key);
  }
  function installSessionBoundary() {
    const logout = app.logout, refresh = app.refreshCapabilities, login = app.loginWithApi;
    let loginAttempt = 0;
    // The source refresh owns mutable flight/timestamp fields. Give each account a separate receiver
    // so a previous account's finally cannot clear or delay the next account's refresh.
    const newOwner = () => Object.create(app, {_capabilityFlight: {value: null, writable: true}, _capabilityCheckedAt: {value: 0, writable: true}});
    let capabilityOwner = newOwner();
    for (const key of ['_capabilityFlight', '_capabilityCheckedAt']) Object.defineProperty(app, key, {configurable: true,
      get() { return capabilityOwner[key]; }, set(value) { capabilityOwner[key] = value; }});
    app.refreshCapabilities = function (...args) { return refresh.apply(capabilityOwner, args); };
    app.loginWithApi = function (...args) {
      const attempt = ++loginAttempt;
      return login.apply(this, args).catch(error => {
        // The source stores auth before validating actor.role. Failed unsupported actors must
        // not leave bearer credentials behind, and a late failure must not clear a new login.
        if (attempt === loginAttempt && error.code !== 'SESSION_CHANGED' && !this.globalData.session) requireModule('utils/apiClient').saveAuth(null);
        throw error;
      });
    };
    app.logout = function (...args) {
      const result = logout.apply(this, args);
      capabilityOwner = newOwner(); clearSessionNavigation();
      window.dispatchEvent(new CustomEvent('sales:session-ended'));
      return result;
    };
  }
  const expressionCache = new Map();
  const activeModals = new Set();
  const pageUiMethods = new Set(['navigateTo', 'navigateBack', 'redirectTo', 'switchTab', 'reLaunch', 'hideTabBar', 'showTabBar', 'setNavigationBarTitle', 'showToast', 'showLoading', 'hideLoading', 'showNavigationBarLoading', 'hideNavigationBarLoading', 'pageScrollTo', 'showModal']);
  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const equal = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object' && JSON.stringify(a) === JSON.stringify(b));
  const camel = text => text.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  const truth = value => !!value && value !== 'false';
  const booleanAttribute = (raw, scope) => raw === '' || truth(value(raw, scope));
  const cssRpx = value => String(value || '').replace(/(-?\d*\.?\d+)rpx/g, 'calc(var(--rpx) * $1)');

  function toast(message, duration = 2500) {
    overlays.querySelectorAll('.wx-toast:not([data-loading])').forEach(el => el.remove());
    const el = document.createElement('div');
    el.className = 'wx-toast'; el.role = 'status'; el.textContent = message;
    overlays.append(el);
    window.dispatchEvent(new CustomEvent('sales:toast', {detail: {message: String(message || '')}}));
    setTimeout(() => el.remove(), duration); return el;
  }
  function report(error) {
    const message = String(error && (error.stack || error.message) || error);
    errors.push(message); console.error(error); toast(error && error.message || '页面运行异常，请刷新重试');
  }
  window.addEventListener('error', event => errors.push(event.message));
  window.addEventListener('unhandledrejection', event => report(event.reason));

  function resolve(path, from = '') {
    const relative = path.startsWith('.');
    const parts = (relative ? from.split('/').slice(0, -1).join('/') + '/' + path : path).split('/');
    const output = [];
    for (const part of parts) { if (part === '..') output.pop(); else if (part && part !== '.') output.push(part); }
    return output.join('/').replace(/\.(js|json)$/, '');
  }
  function requireModule(path, from = '') {
    let id = resolve(path, from);
    if (!(id in bundle.modules) && id + '/index' in bundle.modules) id += '/index';
    if (cache[id]) return cache[id].exports;
    const code = bundle.modules[id];
    if (code === undefined) throw new Error('交付包中缺少模块：' + id);
    const module = cache[id] = {exports: {}};
    executeModule(id, module);
    return module.exports;
  }
  function scopedWx(scope) {
    const methods = new Map();
    return new Proxy(wx, {get(target, key) {
      if (typeof target[key] !== 'function') return target[key];
      if (!methods.has(key)) methods.set(key, (...args) => {
        const owner = scope.owner, page = owner?._page || owner;
        if (owner && pageUiMethods.has(key) && (owner._destroyed || page !== current)) {
          if (key === 'showModal') {
            const result = {confirm: false, cancel: false, errMsg: 'showModal:fail page changed'};
            args[0]?.fail?.(result); args[0]?.complete?.(result); return Promise.resolve(result);
          }
          if (/^(navigate|redirect|switchTab|reLaunch)/.test(key)) args[0]?.fail?.({errMsg: key + ':fail page is no longer active'});
          return;
        }
        if (key === 'showModal') return modal(args[0], owner);
        return target[key](...args);
      });
      return methods.get(key);
    }});
  }
  function scopedTimer(scope, repeating) {
    return (callback, delay, ...args) => {
      const owner = scope.owner;
      const register = repeating ? window.setInterval : window.setTimeout;
      const id = register(() => {
        if (!repeating) owner?._timers?.delete(id);
        if (!owner?._destroyed) callback(...args);
      }, delay);
      if (owner) { owner._timers ||= new Map(); owner._timers.set(id, repeating); }
      return id;
    };
  }
  function executeModule(id, module, scope) {
    const code = bundle.modules[id];
    if (code === undefined) throw new Error('交付包中缺少模块：' + id);
    new Function('require', 'module', 'exports', 'wx', 'App', 'Page', 'Component', 'getApp', 'getCurrentPages',
      'setTimeout', 'setInterval',
      code + '\n//# sourceURL=sales-source/' + id + '.js')(
      path => {
        const exports = requireModule(path, id);
        if (!scope || resolve(path, id) !== 'utils/apiClient') return exports;
        scope.apiProxy ||= new Proxy(exports, {get(target, key) {
          const fn = target[key];
          if (typeof fn !== 'function' || ['getAuth', 'getBaseUrl', 'isEnabled', 'saveAuth', 'updateActor'].includes(key)) return fn;
          return (...args) => {
            if (scope.owner && (scope.owner._destroyed || scope.epoch !== navigationEpoch)) return Promise.reject(Object.assign(new Error('页面或账号已切换，请在当前页面重试'), {code: 'SESSION_CHANGED'}));
            return fn(...args);
          };
        }});
        return scope.apiProxy;
      }, module, module.exports, scope ? scopedWx(scope) : wx,
      definition => { app = definition; }, definition => { if (scope) scope.definition = definition; else definitions[id] = definition; },
      definition => { if (scope) scope.definition = definition; else componentDefinitions[id] = definition; }, () => app, () => stack.slice(),
      scope ? scopedTimer(scope, false) : window.setTimeout.bind(window), scope ? scopedTimer(scope, true) : window.setInterval.bind(window));
  }
  function instanceDefinition(id) {
    const scope = {owner: null, definition: null, epoch: navigationEpoch};
    executeModule(id, {exports: {}}, scope);
    if (!scope.definition) throw new Error('模块未声明 Page 或 Component：' + id);
    return scope;
  }
  function evaluate(expression, scope) {
    try {
      if (!expressionCache.has(expression)) expressionCache.set(expression, new Function('s', 'with(s){return (' + expression + ')}'));
      return expressionCache.get(expression)(scope);
    } catch (_) { return undefined; }
  }
  function value(raw, scope) {
    if (typeof raw !== 'string') return raw;
    const matches = [...raw.matchAll(/{{([\s\S]*?)}}/g)];
    if (matches.length === 1 && matches[0][0] === raw) return evaluate(matches[0][1], scope);
    return raw.replace(/{{([\s\S]*?)}}/g, (_, expression) => {
      const result = evaluate(expression, scope); return result == null ? '' : String(result);
    });
  }
  function dataset(attributes, scope) {
    const result = {};
    for (const key in attributes) if (key.startsWith('data-')) result[camel(key.slice(5))] = value(attributes[key], scope);
    return result;
  }
  function call(owner, fn, ...args) {
    if (!owner || owner._destroyed || typeof fn !== 'function') return;
    try { const result = fn.apply(owner, args); if (result && result.catch) result.catch(report); return result; }
    catch (error) { report(error); }
  }
  function lifecycle(owner, name, ...args) { return call(owner, owner && owner[name], ...args); }
  function componentLifecycle(owner, name, ...args) {
    const definition = owner._definition;
    return call(owner, definition.lifetimes && definition.lifetimes[name] || definition[name], ...args);
  }
  function eventFor(type, data, native, detail = {}) {
    const touches = list => Array.from(list || [], touch => ({identifier:touch.identifier,clientX:touch.clientX,clientY:touch.clientY,pageX:touch.pageX,pageY:touch.pageY}));
    return {type, timeStamp: Date.now(), currentTarget: {dataset: data}, target: {dataset: data}, detail,
      touches: touches(native?.touches), changedTouches: touches(native?.changedTouches),
      preventDefault: () => native && native.preventDefault(), stopPropagation: () => native && native.stopPropagation()};
  }
  function invoke(owner, name, data, native, detail = {}, type = 'tap') {
    if (globalThis.SalesSelect?.intercept(owner, name, data, native, detail, type)) return;
    return call(owner, owner && owner[name], eventFor(type, data, native, detail));
  }
  function readPath(object, path) {
    return path.split('.').reduce((value, key) => value == null ? undefined : value[key], object);
  }
  function observers(owner, changed, initial = false) {
    if (!owner._definition || owner._observing) return;
    const definition = owner._definition;
    owner._observing = true;
    try {
      for (const expression in definition.observers || {}) {
        const paths = expression.split(',').map(path => path.trim());
        if (initial || paths.some(path => path === '**' || changed.some(key => key === path || key.startsWith(path.replace(/\.\*\*$/, '') + '.') || path.startsWith(key + '.')))) {
          call(owner, definition.observers[expression], ...paths.map(path => readPath(owner.data, path)));
        }
      }
      for (const key of changed) {
        const property = definition.properties && definition.properties[key];
        if (property && property.observer) call(owner, typeof property.observer === 'string' ? owner[property.observer] : property.observer, owner.properties[key]);
      }
    } finally { owner._observing = false; }
  }
  function setData(patch, callback) {
    if (this._destroyed) return;
    // The full local CRM uses bounded list rendering; business filtering still
    // runs on every source record. Shared Mini Program sources stay unchanged.
    if (window.SALES_MODE === 'preview' && window.SalesCrmPreview?.transformPatch) patch = window.SalesCrmPreview.transformPatch(this, patch);
    const changed = [];
    for (const rawKey in patch || {}) {
      const path = rawKey.replace(/\[(\d+)\]/g, '.$1').split('.');
      let target = this.data;
      for (let i = 0; i < path.length - 1; i++) {
        if (target[path[i]] == null) target[path[i]] = /^\d+$/.test(path[i + 1]) ? [] : {};
        target = target[path[i]];
      }
      const key = path[path.length - 1];
      if (!equal(target[key], patch[rawKey])) changed.push(path.join('.'));
      target[key] = patch[rawKey];
      if (path.length === 1 && this.properties && key in this.properties) this.properties[key] = patch[rawKey];
    }
    if (changed.length) observers(this, changed);
    const page = this._page || this;
    const restoring = page._scrollRestore;
    if (restoring) {
      for (const key of Object.keys(patch)) if (isLoadingKey(key)) {
        if (!restoring.loading.has(this)) restoring.loading.set(this, new Set());
        restoring.loading.get(this).add(key);
      }
    }
    if (callback) {
      if (page === current) page._callbacks.push(() => call(this, callback));
      else queueMicrotask(() => call(this, callback));
    }
    if (page === current) scheduleRender();
  }
  function selectComponent(owner, selector, all = false) {
    const list = [...owner._components.values()].filter(instance => !instance._destroyed &&
      (selector.startsWith('#') ? instance._attributes.id === selector.slice(1) : selector.startsWith('.') ? String(instance._attributes.class || '').split(/\s+/).includes(selector.slice(1)) : instance._tag === selector));
    return all ? list : list[0] || null;
  }
  function attachMethods(owner) {
    owner.setData = setData;
    owner._components = new Map(); owner._callbacks = [];
    owner.createSelectorQuery = () => selectorQuery(owner);
    owner.selectComponent = selector => selectComponent(owner, selector);
    owner.selectAllComponents = selector => selectComponent(owner, selector, true);
  }
  function propertyDefaults(definition) {
    const result = {};
    for (const key in definition.properties || {}) {
      const property = definition.properties[key];
      const type = typeof property === 'function' ? property : property.type;
      result[key] = property && Object.prototype.hasOwnProperty.call(property, 'value') ? clone(property.value) :
        type === String ? '' : type === Number ? 0 : type === Boolean ? false : type === Array ? [] : null;
    }
    return result;
  }
  function componentFor(owner, id, node, scope, key) {
    let instance = owner._components.get(key);
    if (instance && instance._module !== id) { detach(instance); owner._components.delete(key); instance = null; }
    const instanceScope = instance ? null : instanceDefinition(id);
    const definition = instance ? instance._definition : instanceScope.definition;
    if (!definition) throw new Error('组件未声明 Component：' + id);
    const properties = propertyDefaults(definition), attributes = {};
    for (const name in node.a || {}) {
      const result = value(node.a[name], scope);
      attributes[name] = result;
      const propertyName = camel(name);
      if (propertyName in properties) {
        const property = definition.properties[propertyName];
        const type = typeof property === 'function' ? property : property.type;
        properties[propertyName] = type === Boolean ? booleanAttribute(node.a[name], scope) : result;
      }
    }
    const fresh = !instance;
    if (fresh) {
      instance = Object.assign({}, definition.methods || {}, {data: {...clone(definition.data || {}), ...properties}, properties,
        _definition: definition, _module: id, _owner: owner, _page: owner._page || owner, _id: ++serial,
        _key: key, _tag: node.t, _attributes: attributes, _destroyed: false, _attached: false});
      attachMethods(instance); owner._components.set(key, instance);
      instanceScope.owner = instance;
      instance.triggerEvent = (name, detail = {}, options = {}) => {
        if (instance._destroyed) return;
        const binding = instance._attributes['bind:' + name] || instance._attributes['bind' + name] || instance._attributes['catch:' + name] || instance._attributes['catch' + name];
        if (binding) invoke(instance._owner, binding, instance._dataset, null, detail, name);
        if (options.bubbles && !binding && instance._owner.triggerEvent) instance._owner.triggerEvent(name, detail, options);
      };
    }
    instance._attributes = attributes; instance._dataset = dataset(node.a || {}, scope); instance._seen = true;
    const changed = fresh ? Object.keys(properties) : Object.keys(properties).filter(name => !equal(instance.properties[name], properties[name]));
    for (const name of changed) { instance.properties[name] = properties[name]; instance.data[name] = properties[name]; }
    if (fresh) componentLifecycle(instance, 'created');
    if (changed.length) observers(instance, changed, fresh);
    if (fresh) { instance._attached = true; componentLifecycle(instance, 'attached'); instance._page._readyComponents.push(instance); }
    return instance;
  }

  function textNode(text, key) { return {tag: '#text', key, text: String(text == null ? '' : text)}; }
  function templateText(nodes) { return (nodes || []).map(node => node.tag === '#text' ? node.text : templateText(node.children)).join(' '); }
  function children(nodes, scope, owner, path) {
    const output = []; let chain = false, taken = false;
    (nodes || []).forEach((node, index) => {
      const key = path + '.' + index;
      if (typeof node === 'string') { if (node.trim()) output.push(textNode(value(node, scope), key)); return; }
      const attributes = node.a || {};
      if ('wx:for' in attributes) { output.push(...buildNode(node, scope, owner, key)); chain = false; return; }
      if ('wx:if' in attributes) { chain = true; taken = !!value(attributes['wx:if'], scope); if (!taken) return; }
      else if ('wx:elif' in attributes) { const show = chain && !taken && !!value(attributes['wx:elif'], scope); taken ||= show; if (!show) return; }
      else if ('wx:else' in attributes) { const show = chain && !taken; taken = true; if (!show) return; }
      else { chain = false; taken = false; }
      output.push(...buildNode(node, scope, owner, key, true));
    });
    return output;
  }
  function buildNode(node, scope, owner, key, conditionHandled = false) {
    const attributes = node.a || {};
    if ('wx:for' in attributes) {
      const list = value(attributes['wx:for'], scope) || [];
      const entries = Array.isArray(list) ? list.map((item, index) => [index, item]) : Object.entries(list);
      const seen = new Map();
      return entries.flatMap(([index, item]) => {
        const local = Object.assign(Object.create(scope), {[attributes['wx:for-item'] || 'item']: item, [attributes['wx:for-index'] || 'index']: index});
        const attrs = {...attributes}; delete attrs['wx:for'];
        let itemKey = attrs['wx:key'] === '*this' ? item : attrs['wx:key'] ? item && item[attrs['wx:key']] : index;
        if (itemKey == null || typeof itemKey === 'object') itemKey = index;
        const count = seen.get(String(itemKey)) || 0; seen.set(String(itemKey), count + 1);
        return buildNode({...node, a: attrs}, local, owner, key + '.i:' + String(itemKey) + ':' + count);
      });
    }
    if (!conditionHandled && 'wx:if' in attributes && !value(attributes['wx:if'], scope)) return [];
    if (node.t === 'block') return children(node.c, scope, owner, key);
    const record = owner._definition ? bundle.components[owner._module] : bundle.pages[owner.route];
    if (node.t === 'import' || node.t === 'template' && attributes.name) return [];
    if (node.t === 'template') {
      const name = value(attributes.is, scope), tree = record.templates?.[name];
      if (!tree) throw new Error('交付包中缺少命名模板：' + name);
      const expression = String(attributes.data || '{{}}').replace(/^{{|}}$/g, '');
      const data = expression.trim() ? evaluate('({' + expression + '})', scope) : {};
      return children(tree, data || {}, owner, key + '.template:' + name);
    }
    const componentPath = (record.config && record.config.usingComponents || {})[node.t] || (bundle.config.usingComponents || {})[node.t];
    if (componentPath) {
      const id = resolve(componentPath, owner._module || owner.route);
      if (!bundle.components[id]) throw new Error('交付包中缺少组件模板：' + id);
      const component = componentFor(owner, id, node, scope, key);
      return [{tag: node.t, key, attrs: {id: component._attributes.id || '', class: component._attributes.class || '', 'data-wx-component': String(component._id), 'data-wx-style': owner._module || owner.route}, events: {}, component,
        children: children(bundle.components[id].tree, component.data, component, key + '.component')}];
    }
    const tag = node.t === 'root-portal' ? 'dialog' : node.t === 'image' ? 'img' : node.t === 'navigator' ? 'view' : node.t === 'checkbox' || node.t === 'radio' ? 'input' : node.t;
    const vnode = {tag, key, attrs: {'data-wx-style': owner._module || owner.route}, events: {}, children: [], owner};
    if (node.t === 'root-portal') {
      vnode.portal = true;
      vnode.attrs['data-wx-portal'] = '';
      vnode.attrs['aria-modal'] = 'true';
      vnode.attrs['aria-label'] = owner._module?.includes('quarter-target') ? '季度目标' : '筛选团队成员';
      vnode.events.cancel = event => { event.preventDefault(); call(owner, owner.cancel || owner.close); };
      vnode.events.keydown = event => {
        if (event.key !== 'Tab') return;
        const controls = [...event.currentTarget.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex="0"]')].filter(el=>el.getClientRects().length);
        if (!controls.length) return;
        event.preventDefault();
        const index = controls.indexOf(document.activeElement);
        controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
      };
    }
    const data = dataset(attributes, scope);
    for (const name in attributes) {
      if (name.startsWith('wx:') || /^(bind|catch)/.test(name) || ['range', 'range-key', 'value', 'checked', 'focus', 'scroll-into-view', 'scroll-top', 'scroll-left'].includes(name)) continue;
      let result = value(attributes[name], scope);
      if (name === 'src' && tag === 'img') result = bundle.images[String(result || '').replace(/^\//, '')] || result;
      if (name === 'style') result = cssRpx(result);
      if (name === 'canvas-id') { vnode.attrs.id = result; vnode.attrs['data-canvas-id'] = result; continue; }
      if (name === 'password') { if (booleanAttribute(attributes[name], scope)) vnode.attrs.type = 'password'; continue; }
      if (['disabled', 'multiple', 'autofocus', 'hidden', 'scroll-x', 'scroll-y'].includes(name)) { if (booleanAttribute(attributes[name], scope)) vnode.attrs[name] = ''; continue; }
      if (result !== undefined && result !== null) vnode.attrs[name] = String(result);
    }
    if (tag === 'input' || tag === 'textarea') {
      vnode.value = value(attributes.value == null ? '' : attributes.value, scope) ?? '';
      if (node.t === 'checkbox' || node.t === 'radio') {
        vnode.attrs.type = node.t; vnode.checked = !!value(attributes.checked, scope);
      } else if (attributes.type === 'digit') { vnode.attrs.type = 'text'; vnode.attrs.inputmode = 'decimal'; }
      else if (attributes.type === 'number') { vnode.attrs.type = 'text'; vnode.attrs.inputmode = 'numeric'; }
      if (attributes.placeholder) vnode.attrs['aria-label'] = value(attributes.placeholder, scope);
      if (tag === 'textarea' && 'auto-height' in attributes) vnode.autoHeight = true;
      vnode.focusWanted = 'focus' in attributes && booleanAttribute(attributes.focus, scope);
    }
    const tap = attributes.bindtap || attributes['bind:tap'] || attributes.catchtap || attributes['catch:tap'];
    if (tap) {
      const name = value(tap, scope), caught = 'catchtap' in attributes || 'catch:tap' in attributes;
      vnode.attrs.class = (vnode.attrs.class || '') + ' wx-interactive'; vnode.attrs['data-handler'] = name;
      if (!['button', 'input', 'textarea'].includes(tag)) { vnode.attrs.role ||= 'button'; vnode.attrs.tabindex = '0'; }
      const action = event => { if (caught) event.stopPropagation(); if (!event.currentTarget.disabled) invoke(owner, name, data, event); };
      vnode.events.click = action;
      if (tag !== 'button') vnode.events.keydown = event => { if (event.target === event.currentTarget && ['Enter', ' '].includes(event.key)) { event.preventDefault(); action(event); } };
    }
    for (const kind of ['input', 'change', 'confirm', 'focus', 'blur']) {
      const name = attributes['bind' + kind] || attributes['bind:' + kind];
      if (!name || tag === 'picker') continue;
      vnode.events[kind === 'confirm' ? 'keydown' : kind] = event => {
        if (kind === 'confirm' && (event.key !== 'Enter' || event.isComposing || event.keyCode === 229)) return;
        const el = event.currentTarget;
        const inputValue = tag === 'checkbox-group' ? [...el.querySelectorAll('input[type=checkbox]:checked')].map(box => box.value) : el.value;
        invoke(owner, value(name, scope), data, event, {value: inputValue, cursor: el.selectionStart}, kind);
      };
    }
    for (const kind of ['touchstart','touchmove','touchend','touchcancel']) {
      const caught = attributes['catch'+kind] || attributes['catch:'+kind];
      const handler = caught || attributes['bind'+kind] || attributes['bind:'+kind];
      if (!handler) continue;
      vnode.events[kind] = event => {
        if (caught) { event.stopPropagation(); if (kind === 'touchmove') event.preventDefault(); }
        invoke(owner, value(handler, scope), data, event, {}, kind);
      };
    }
    vnode.children = children(node.c, scope, owner, key);
    const invalidTaskDue = owner.route === 'pages/management-task-create/index' && typeof owner.parseCustomDue === 'function' &&
      owner.data.customDueDate && owner.data.customDueTime && owner.parseCustomDue().getTime() <= Date.now();
    if (invalidTaskDue && String(vnode.attrs.class || '').split(/\s+/).includes('custom-due-fields')) {
      vnode.children.push({tag: 'p', key: key + '.due-error', attrs: {id: 'web-task-due-error', class: 'wx-date-field-error', role: 'alert'}, events: {},
        children: [textNode('截止时间需要晚于当前时间，请调整日期或时间。', key + '.due-error.text')]});
    }
    if (tag === 'picker') {
      const mode = value(attributes.mode || 'selector', scope);
      const temporal = mode === 'date' || mode === 'time';
      const control = {tag: temporal ? 'button' : 'select', key: key + '.control', attrs: {class: temporal ? 'wx-temporal-trigger' : 'wx-picker-control', 'aria-label': '选择'}, events: {}, children: []};
      if (temporal) {
        control.attrs.type = 'button'; control.attrs['data-date-mode'] = mode;
        control.attrs['data-min'] = attributes.start ? value(attributes.start, scope) || '' : '';
        control.attrs['data-max'] = attributes.end ? value(attributes.end, scope) || '' : '';
        control.attrs['aria-haspopup'] = 'dialog'; control.attrs['aria-controls'] = 'web-date-dialog';
        control.attrs['aria-expanded'] = 'false';
        if (invalidTaskDue) { control.attrs['aria-invalid'] = 'true'; control.attrs['aria-describedby'] = 'web-task-due-error'; }
      } else {
        const range = value(attributes.range, scope) || [], field = attributes['range-key'];
        control.children = range.map((item, index) => ({tag: 'option', key: key + '.option.' + index, attrs: {value: String(index)}, events: {}, children: [textNode(field ? item[field] : item, key + '.option.' + index + '.text')]}));
      }
      control.value = value(attributes.value, scope) ?? (mode === 'selector' ? 0 : '');
      // WeChat's picker can confirm its initial cursor. HTML select cannot fire a
      // change for its already-selected first option, so keep an explicit empty
      // choice while the source template still says that selection is required.
      const pickerLabel = templateText(vnode.children).trim();
      if (mode === 'selector' && /请选择/.test(pickerLabel)) {
        control.children.unshift({tag: 'option', key: key + '.placeholder', attrs: {value: '', disabled: '', hidden: ''}, events: {}, children: [textNode(pickerLabel, key + '.placeholder.text')]});
        control.value = '';
      }
      control.attrs['aria-label'] = temporal ? (mode === 'date' ? '选择日期' : '选择时间') + (pickerLabel ? ' · ' + pickerLabel : '') : pickerLabel || '选择';
      if ('disabled' in attributes && booleanAttribute(attributes.disabled, scope)) control.attrs.disabled = '';
      if (temporal) {
        control.events.click = event => {
          event.stopPropagation(); const anchor = event.currentTarget;
          SalesDatePicker.open({anchor, mode, value: anchor.value, min: anchor.dataset.min, max: anchor.dataset.max,
            onCommit: selected => {
              if (!anchor.isConnected || anchor.disabled || owner._destroyed || (owner._page || owner) !== current) return;
              invoke(owner, value(attributes.bindchange || attributes['bind:change'], scope), data, event, {value: selected}, 'change');
            }});
        };
      } else control.events.change = event => { event.stopPropagation(); invoke(owner, value(attributes.bindchange || attributes['bind:change'], scope), data, event, {value: mode === 'selector' ? Number(event.currentTarget.value) : event.currentTarget.value}, 'change'); };
      vnode.children.push(control);
    }
    if (tag === 'scroll-view') {
      vnode.scrollTarget = value(attributes['scroll-into-view'], scope);
      vnode.scrollTop = value(attributes['scroll-top'], scope); vnode.scrollLeft = value(attributes['scroll-left'], scope);
      const lower = attributes.bindscrolltolower || attributes['bind:scrolltolower'];
      const scroll = attributes.bindscroll || attributes['bind:scroll'];
      if (lower || scroll) vnode.events.scroll = event => {
        if (owner._destroyed || (owner._page || owner) !== current) return;
        const el = event.currentTarget;
        const detail = {scrollTop: el.scrollTop, scrollLeft: el.scrollLeft, scrollHeight: el.scrollHeight, scrollWidth: el.scrollWidth};
        if (scroll) invoke(owner, value(scroll, scope), data, event, detail, 'scroll');
        if (!lower) return;
        const threshold = Math.max(0, Number(value(attributes['lower-threshold'] ?? 50, scope)) || 0);
        const atBottom = el.scrollHeight > el.clientHeight + 1 && el.scrollTop + el.clientHeight >= el.scrollHeight - threshold;
        if (!atBottom) { el._wxLowerEdge = null; return; }
        // One event per edge/loaded extent; a render does not re-arm the same edge.
        if (el._wxLowerEdge === el.scrollHeight) return;
        el._wxLowerEdge = el.scrollHeight;
        invoke(owner, value(lower, scope), data, event, {direction: 'bottom'}, 'scrolltolower');
      };
    }
    return [vnode];
  }

  function patchChildren(parent, vnodes) {
    const existing = new Map([...parent.childNodes].map(node => [node._wxKey, node]));
    let position = parent.firstChild;
    for (const vnode of vnodes) {
      let node = existing.get(vnode.key);
      const matches = node && (vnode.tag === '#text' ? node.nodeType === 3 : node.nodeType === 1 && node.localName === vnode.tag);
      if (!matches) node = vnode.tag === '#text' ? document.createTextNode(vnode.text) : document.createElement(vnode.tag);
      existing.delete(vnode.key); node._wxKey = vnode.key;
      if (node !== position) parent.insertBefore(node, position);
      if (vnode.tag === '#text') { if (node.nodeValue !== vnode.text) node.nodeValue = vnode.text; }
      else patchElement(node, vnode);
      position = node.nextSibling;
    }
    while (position) { const next = position.nextSibling; position.remove(); position = next; }
  }
  function patchElement(el, vnode) {
    const attrs = {...vnode.attrs, 'data-wx-key': vnode.key};
    // Native activity disclosures belong to this keyed DOM row. A data refresh
    // must not close the receipt the user is reading; removing the row resets it.
    const nativeDisclosure = vnode.tag === 'details' && String(attrs.class || '').split(/\s+/).includes('web-activity-detail');
    for (const name of el.getAttributeNames()) if (!(name in attrs) && !(vnode.chart && ['style', '_echarts_instance_'].includes(name)) && !(vnode.tag === 'canvas' && ['width', 'height'].includes(name)) && !((vnode.portal || nativeDisclosure) && name === 'open')) el.removeAttribute(name);
    for (const name in attrs) if (el.getAttribute(name) !== String(attrs[name])) el.setAttribute(name, attrs[name]);
    el._wxEvents = vnode.events || {};
    el._wxListeners ||= new Set();
    for (const name in el._wxEvents) if (!el._wxListeners.has(name)) {
      el.addEventListener(name, event => el._wxEvents[name] && el._wxEvents[name](event)); el._wxListeners.add(name);
    }
    if (vnode.component) vnode.component._element = el;
    if (vnode.chart) globalThis.SalesDashboard?.update(el, vnode.chart);
    else patchChildren(el, vnode.children || []);
    if (vnode.portal && !el.open) el.showModal();
    if (vnode.value !== undefined && el.value !== String(vnode.value)) {
      const focused = document.activeElement === el, start = el.selectionStart, end = el.selectionEnd;
      el.value = vnode.value;
      if (focused && start !== null) try { el.setSelectionRange(Math.min(start, el.value.length), Math.min(end, el.value.length)); } catch (_) {}
    }
    if (vnode.checked !== undefined) el.checked = vnode.checked;
    if (vnode.focusWanted && !el._wxFocusWanted) requestAnimationFrame(() => {
      const page = vnode.owner?._page || vnode.owner;
      if (el.isConnected && !el.disabled && page === current && !vnode.owner?._destroyed) el.focus({preventScroll: true});
    });
    el._wxFocusWanted = !!vnode.focusWanted;
    if (vnode.autoHeight) { el.style.height = 'auto'; el.style.height = Math.max(el.scrollHeight, 64) + 'px'; }
    if (vnode.scrollTop != null && vnode.scrollTop !== el._wxScrollTop) { el.scrollTop = Number(vnode.scrollTop); el._wxScrollTop = vnode.scrollTop; }
    if (vnode.scrollLeft != null && vnode.scrollLeft !== el._wxScrollLeft) { el.scrollLeft = Number(vnode.scrollLeft); el._wxScrollLeft = vnode.scrollLeft; }
    if (vnode.scrollTarget && vnode.scrollTarget !== el._wxScrollTarget) {
      el._wxScrollTarget = vnode.scrollTarget;
      requestAnimationFrame(() => {
        const page = vnode.owner?._page || vnode.owner;
        if (!el.isConnected || page !== current || vnode.owner?._destroyed) return;
        const target = [...el.querySelectorAll('[id]')].find(node => node.id === vnode.scrollTarget);
        if (target) {
          const scroll = globalThis.SalesDetailWorkspace?.scrollContainer(el, target) || el;
          scroll.scrollTop += target.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
        }
      });
    }
  }
  function walkComponents(owner, fn) { for (const instance of owner._components.values()) { fn(instance); walkComponents(instance, fn); } }
  function prune(owner) { for (const [key, instance] of owner._components) { if (!instance._seen) { detach(instance); owner._components.delete(key); } else prune(instance); } }
  function detach(instance) {
    if (instance._destroyed) return;
    for (const child of instance._components.values()) detach(child);
    componentLifecycle(instance, 'detached'); instance._destroyed = true; instance._components.clear(); clearOwnerTimers(instance);
  }
  function render() {
    frame = 0;
    const page = current;
    if (!page || page._destroyed || rendering) return;
    rendering = true;
    try {
      walkComponents(page, instance => { instance._seen = false; });
      globalThis.SalesDetailWorkspace?.configure(page);
      globalThis.SalesReviewForms?.configure(page, {listCustomers: async options => {
        const epoch = navigationEpoch;
        const check = () => { if (current !== page || page._destroyed || navigationEpoch !== epoch) throw new Error('页面或账号已切换，请在当前页面重试'); };
        check();
        const result = await requireModule('utils/apiClient').listCustomers(options);
        check();
        return result;
      }});
      let data = page.route === 'pages/index/index' ? homePresentation(page) : page.data;
      if (globalThis.SalesDashboard) data = SalesDashboard.presentation(page, data);
      if (globalThis.SalesTasks) data = SalesTasks.presentation(page, data);
      if (globalThis.SalesDepartmentUI?.render(page, data, root)) {
        prune(page);
        while (page._readyComponents.length) componentLifecycle(page._readyComponents.shift(), 'ready');
        const callbacks = page._callbacks.splice(0); for (const callback of callbacks) callback();
        notifyNavigation(); restorePageScroll(page);
        return;
      }
      let tree = children(bundle.pages[page.route].tree, data, page, 'page:' + page._id);
      if (globalThis.SalesDashboard) tree = SalesDashboard.adapt(tree, page);
      if (globalThis.SalesReviewPages) tree = SalesReviewPages.adapt(tree, page);
      if (current !== page) return;
      patchChildren(root, tree); prune(page); globalThis.SalesDatePicker?.reconcile(); globalThis.SalesSelect?.reconcile(); globalThis.SalesDashboard?.reconcile();
      while (page._readyComponents.length) componentLifecycle(page._readyComponents.shift(), 'ready');
      const callbacks = page._callbacks.splice(0); for (const callback of callbacks) callback();
      notifyNavigation();
      restorePageScroll(page);
    } catch (error) { report(error); }
    finally { rendering = false; }
  }
  const activityViewport = window.matchMedia('(min-width: 601px)');
  activityViewport.addEventListener('change', () => scheduleRender());
  function homePresentation(page) {
    globalThis.SalesHomeActivity.configure(page);
    // Reorder server-provided counters by task intent; never count notification cards.
    const metrics = new Map((page.data.overviewMetrics || []).map(item => [item.key, item]));
    const definitions = [
      ['today_pending', '今日待办', '今天截止的未完成任务'],
      ['all_pending', '全部待办', '包含逾期、今天与未来'],
      ['today_completed', '今日已完成', '今天完成的任务']
    ];
    return {...page.data,
      webActivityCompact: activityViewport.matches,
      webOverviewMetrics: definitions.map(([key, label, hint]) => {
        const value = metrics.get(key)?.value;
        const valid = (typeof value === 'string' || typeof value === 'number') && String(value).trim() !== '' && Number.isInteger(Number(value)) && Number(value) >= 0;
        return {key, label, hint, value: valid ? value : '—'};
      }),
      // Welcome already appears above the metrics. Keep every business event and its source order.
      ...globalThis.SalesHomeActivity.presentation(page.data,page._webActivityFilter,page._webActivityQuery),
      webHomeOrderLabel: page.homeOrder === 'asc' ? '按时间正序' : '最近更新在前'
    };
  }
  function scheduleRender() { if (!frame) frame = requestAnimationFrame(render); }
  // Web frames scroll inside their content; natural mobile pages and login use the document.
  // Keep the Mini Program's page-scroll API and pagination bound to the active surface.
  const desktopViewport = window.matchMedia('(min-width: 601px)');
  function usesContentScroll(page = current) {
    return !!page && page.route !== 'pages/login/index' && (desktopViewport.matches
      || getComputedStyle(root).getPropertyValue('--web-scroll-surface').trim() === 'content');
  }
  function pageScrollHost() {
    if (!usesContentScroll()) return document.scrollingElement;
    return [...root.querySelectorAll('[data-web-page-scroll]')].find(el => el.clientHeight > 0
      && /auto|scroll/.test(getComputedStyle(el).overflowY) && el.getClientRects().length) || root;
  }
  function pageScrollMetrics() {
    const content = usesContentScroll(), el = pageScrollHost();
    return {top: content ? el.scrollTop : window.scrollY, height: content ? el.clientHeight : innerHeight,
      total: el?.scrollHeight || 0};
  }
  function runtimeWindowInfo() {
    const width = root.clientWidth || innerWidth, height = usesContentScroll() ? root.clientHeight : innerHeight;
    return {platform: 'web', windowWidth: width, windowHeight: height, pixelRatio: devicePixelRatio || 1,
      safeArea: {top: 0, left: 0, width, height, bottom: height, right: width}};
  }
  function scrollPageTo(top, behavior = 'instant') {
    const content = usesContentScroll();
    (content ? pageScrollHost() : window).scrollTo({top: Math.max(0, Number(top) || 0), behavior});
    if (current) {
      current._contentScroll = content;
      current._scrollY = pageScrollMetrics().top;
    }
  }
  function pageScrollTo(options = {}) {
    cancelScrollRestore();
    let top = options.scrollTop || 0;
    if (options.selector) {
      const target = root.querySelector(options.selector);
      if (!target) return;
      const content = usesContentScroll(), host = content ? pageScrollHost() : document.documentElement;
      top = pageScrollMetrics().top + target.getBoundingClientRect().top - (content ? host.getBoundingClientRect().top : 0)
        - (parseFloat(getComputedStyle(host).scrollPaddingTop) || 0)
        - (parseFloat(getComputedStyle(target).scrollMarginTop) || 0);
    }
    scrollPageTo(top, options.duration ? 'smooth' : 'instant');
  }
  function cancelScrollRestore() {
    if (current?._scrollRestore) { current._scrollRestore = null; current._scrollY = pageScrollMetrics().top; }
  }
  function isLoadingKey(key) { return /^(?:loading(?:[A-Z].*)?|.*Loading)$/.test(key); }
  function restorePageScroll(page) {
    const pending = page._scrollRestore;
    if (current !== page || !pending || pending.showPending) return;
    // Only loading fields written during this return belong to its refresh.
    // Hidden role branches may retain a default loading=true forever; active
    // child components report their own setData state through the same path.
    for (const [owner, keys] of pending.loading) {
      if (!owner._destroyed && [...keys].some(key => owner.data[key] === true)) return;
    }
    scrollPageTo(pending.top);
    page._scrollRestore = null;
  }
  function pageComponentsLifecycle(page, name) { walkComponents(page, instance => call(instance, instance._definition.pageLifetimes && instance._definition.pageLifetimes[name])); }
  function hidePage(page) {
    if (!page) return; globalThis.SalesDatePicker?.close({restoreFocus: false}); lifecycle(page, 'onHide'); pageComponentsLifecycle(page, 'hide');
    for (const record of [...activeModals]) if ((record.owner?._page || record.owner) === page) record.finish(false, false, true);
    page._scrollY = page._scrollRestore?.top ?? pageScrollMetrics().top;
    globalThis.SalesDepartmentUI?.unmount();
    page._scrollRestore = null; page._fragment = document.createDocumentFragment();
    while (root.firstChild) page._fragment.append(root.firstChild);
  }
  function unload(page) {
    if (!page || page._destroyed) return;
    lifecycle(page, 'onUnload'); for (const instance of page._components.values()) detach(instance);
    page._components.clear(); page._destroyed = true; page._fragment = null; clearOwnerTimers(page);
    if (tabPages.get(page.route) === page) tabPages.delete(page.route);
  }
  function clearOwnerTimers(owner) {
    for (const [id, repeating] of owner._timers || []) (repeating ? window.clearInterval : window.clearTimeout)(id);
    owner._timers?.clear();
  }
  function pageCss(id) {
    const seen = new Set(), output = [bundle.css || '', bundle.pages[id].css || ''];
    function collect(record, from) {
      for (const raw of Object.values(record.config && record.config.usingComponents || {})) {
        const path = resolve(raw, from); if (seen.has(path)) continue; seen.add(path);
        const component = bundle.components[path]; if (component) { output.push(component.css || ''); collect(component, path); }
      }
    }
    collect(bundle.pages[id], id);
    return output.map(cssRpx).join('\n').replace(/(^|[},]\s*)page(?=[\s.{:#>\[])/g, '$1#page-root');
  }
  function updateHash(page, replace = false) {
    const url = '#' + page._url;
    history[replace || location.hash === url ? 'replaceState' : 'pushState']({salesPath: page._url, salesEpoch: navigationEpoch}, '', url);
  }
  // Keep an in-page filter selection in the current history entry. Do not
  // re-create the page, drop scope parameters, or add a navigation level.
  function replacePageQuery(page, changes) {
    if (page !== current || page._destroyed) return false;
    const url = new URL(page._url, 'https://sales.local/');
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === undefined) url.searchParams.delete(key);
      else url.searchParams.set(key, String(value));
    }
    page._url = url.pathname + url.search;
    page.options = Object.fromEntries(url.searchParams);
    updateHash(page, true);
    return true;
  }
  function notifyNavigation() {
    if (!bundle || !current) return;
    const config = bundle.pages[current.route].config || {}, session = app && app.globalData && app.globalData.session;
    const tabs = (bundle.config.tabBar && bundle.config.tabBar.list || []).map(tab => ({...tab, active: tab.pagePath === current.route,
      icon: bundle.images[tab.pagePath === current.route ? tab.selectedIconPath : tab.iconPath]}));
    const title = titleOverride || config.navigationBarTitleText || bundle.config.window && bundle.config.window.navigationBarTitleText || '商汤销售小浣熊';
    window.dispatchEvent(new CustomEvent('sales:navigation', {detail: {path: current.route, title, tab: tabs.some(tab => tab.active), hidden: hiddenTabs, session, tabs}}));
  }
  function route(path, options = {}) {
    if (!bundle) throw new Error('SalesRuntime 尚未启动');
    const url = new URL(String(path).replace(/^#/, ''), 'https://sales.local/');
    const id = url.pathname.replace(/^\//, '');
    if (!bundle.pages[id]) { const error = new Error('交付包中没有此页面：' + id); toast(error.message); throw error; }
    const hasSession = authenticated();
    if (hasSession) sessionStorage.removeItem(sessionKey('signed-out'));
    if (pendingDeepLink && hasSession && id === bundle.config.tabBar?.list?.[0]?.pagePath) {
      const destination = pendingDeepLink; pendingDeepLink = '';
      sessionStorage.removeItem('sales-web:pending-path:' + (window.SALES_MODE || 'live'));
      return route(destination, {...options, reset: true});
    }
    if (!hasSession && id !== 'pages/login/index') {
      if (!sessionStorage.getItem(sessionKey('signed-out'))) {
        pendingDeepLink = '/' + id + url.search;
        sessionStorage.setItem(sessionKey('pending-path'), pendingDeepLink);
      }
      return route('/pages/login/index', {reset: true});
    }
    hidePage(current);
    if (options.reset) { [...new Set([...stack, ...tabPages.values()])].forEach(unload); stack = []; tabPages.clear(); }
    else if (options.tab) {
      stack.filter(page => !tabPages.has(page.route)).forEach(unload); stack = [];
      const cached = tabPages.get(id);
      if (cached && !cached._destroyed) {
        stack.push(cached); showStoredPage(cached, {...options, push: true}); return cached;
      }
    }
    else if (options.replace) unload(stack.pop());
    const instanceScope = instanceDefinition(id), definition = instanceScope.definition;
    if (!definition) throw new Error('页面未声明 Page：' + id);
    const page = {...definition, data: clone(definition.data || {}), route: id, options: Object.fromEntries(url.searchParams),
      _id: ++serial, _url: '/' + id + url.search, _module: id, _destroyed: false, _readyComponents: [],
      getOpenerEventChannel: () => options.eventChannel || createEventChannel(null)};
    globalThis.SalesTasks?.configure(page);
    attachMethods(page); installVisitExit(page); current = page; stack.push(page); hiddenTabs = false; titleOverride = '';
    instanceScope.owner = page;
    if (bundle.config.tabBar?.list?.some(tab => tab.pagePath === id)) tabPages.set(id, page);
    root.replaceChildren(); style.textContent = pageCss(id);
    if (!options.history) updateHash(page, options.reset || options.replace);
    notifyNavigation(); scrollPageTo(0); lifecycle(page, 'onLoad', page.options);
    if (current !== page) return page;
    render(); lifecycle(page, 'onShow');
    requestAnimationFrame(() => { if (current === page && !page._destroyed) { render(); lifecycle(page, 'onReady'); } });
    return page;
  }
  function showStoredPage(page, options = {}) {
    const pending = {top: page._scrollY || 0, showPending: true, loading: new Map()};
    page._scrollRestore = pending;
    current = page; hiddenTabs = false; titleOverride = ''; style.textContent = pageCss(page.route);
    root.replaceChildren(); if (page._fragment) root.append(page._fragment);
    if (!options.history) updateHash(page, !options.push);
    render(); const shown = lifecycle(page, 'onShow'); pageComponentsLifecycle(page, 'show');
    const ready = () => {
      if (current === page && !page._destroyed && page._scrollRestore === pending) { pending.showPending = false; scheduleRender(); }
    };
    if (shown?.then) shown.then(ready, ready); else ready();
    notifyNavigation();
  }
  // Web navigation exits the native visit editor through its own save/discard flow.
  // Source-driven next steps (structure/confirm/archive) continue to use wx navigation.
  function installVisitExit(page) {
    if (page.route !== 'pages/visit-entry/index' || typeof page.requestBack !== 'function') return;
    const requestBack = page.requestBack, leavePage = page.leavePage, epoch = navigationEpoch;
    page.requestBack = (...args) => { page._webExitDestination = null; return requestBack.apply(page, args); };
    page.leavePage = (...args) => {
      const destination = page._webExitDestination; page._webExitDestination = null;
      if (destination) {
        if (current !== page || page._destroyed || epoch !== navigationEpoch) return;
        page.discardUndo(); return destination();
      }
      return leavePage.apply(page, args);
    };
    page._requestWebExit = destination => {
      if (page.backPromptOpen || page.exitAfterRecording) return;
      page._webExitDestination = destination;
      return requestBack.call(page);
    };
  }
  function userNavigation(action) {
    return current?._requestWebExit ? current._requestWebExit(action) : action();
  }
  function back(delta = 1) {
    if (!authenticated()) return route('/pages/login/index', {reset: true});
    if (stack.length < 2) return route(bundle.config.tabBar.list[0].pagePath, {tab: true});
    hidePage(current); const count = Math.min(Math.max(1, delta), stack.length - 1);
    for (let index = 0; index < count; index++) unload(stack.pop());
    showStoredPage(stack[stack.length - 1]);
  }
  function hashNavigation() {
    if (!bundle) return;
    const addressMode = new URL(location.href).searchParams.get('mode') === 'preview' ? 'preview' : 'live';
    if (addressMode !== window.SALES_MODE) { location.reload(); return; }
    if (history.state?.salesEpoch && history.state.salesEpoch !== navigationEpoch) {
      return route(authenticated() ? '/' + bundle.config.tabBar.list[0].pagePath : '/pages/login/index', {reset: true});
    }
    if (!authenticated()) return route('/pages/login/index', {reset: true});
    const path = location.hash.slice(1) || '/' + bundle.config.pages[0];
    if (path === current?._url) return;
    const guarded = !!current?._requestWebExit;
    if (guarded) updateHash(current, true);
    return userNavigation(() => {
      const index = stack.findIndex(page => page._url === path);
      if (index >= 0) { hidePage(current); while (stack.length > index + 1) unload(stack.pop()); showStoredPage(stack[index], {history: !guarded}); }
      else route(path, {replace: true, history: !guarded, tab: bundle.config.tabBar?.list?.some(tab => '/' + tab.pagePath === path.split('?')[0])});
    });
  }
  window.addEventListener('hashchange', hashNavigation);
  window.addEventListener('popstate', hashNavigation);
  window.addEventListener('pagehide', () => { if (current?.route === 'pages/visit-entry/index') lifecycle(current, 'onHide'); });

  function modal(options = {}, owner = current) {
    return new Promise(resolvePromise => {
      const mask = document.createElement('div'), box = document.createElement('div');
      mask.className = 'wx-modal-mask'; mask.setAttribute('role', 'dialog'); mask.setAttribute('aria-modal', 'true');
      box.className = 'wx-modal';
      const heading = document.createElement('div'); heading.className = 'wx-modal-head'; heading.textContent = options.title || '提示';
      const content = document.createElement('div'); content.className = 'wx-modal-content'; content.textContent = options.content || '';
      const buttons = document.createElement('div'); buttons.className = 'wx-modal-buttons';
      const previousFocus = document.activeElement;
      let finished = false;
      let input;
      if (options.editable) { input = document.createElement('textarea'); input.className = 'wx-modal-input'; input.value = options.content || ''; input.placeholder = options.placeholderText || ''; content.textContent = ''; content.append(input); }
      const finish = (confirm, restoreFocus = true, dismissed = false) => {
        if (finished) return; finished = true;
        activeModals.delete(record); document.removeEventListener('keydown', trapKeys, true); document.removeEventListener('focusin', trapFocus, true); mask.remove();
        const result = {confirm: !dismissed && confirm, cancel: !dismissed && !confirm, content: input ? input.value : '', ...(dismissed ? {errMsg: 'showModal:fail page changed'} : {})};
        try { if (dismissed) options.fail?.(result); else options.success?.(result); options.complete?.(result); } catch (error) { report(error); }
        resolvePromise(result);
        if (restoreFocus) requestAnimationFrame(() => { if ((owner?._page || owner) === current && previousFocus?.isConnected) previousFocus.focus({preventScroll: true}); });
      };
      const record = {owner, finish};
      const isTop = () => [...activeModals].at(-1) === record;
      const controls = () => [...box.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')];
      const trapFocus = event => { if (isTop() && !mask.contains(event.target)) controls()[0]?.focus({preventScroll: true}); };
      const trapKeys = event => {
        if (!isTop()) return;
        if (event.key === 'Escape' && options.showCancel !== false) { event.preventDefault(); event.stopPropagation(); finish(false); }
        else if (event.key === 'Tab') {
          const available = controls(), index = available.indexOf(document.activeElement);
          if (event.shiftKey && index <= 0 || !event.shiftKey && (index < 0 || index === available.length - 1)) {
            event.preventDefault(); (event.shiftKey ? available.at(-1) : available[0])?.focus();
          }
        }
      };
      const add = (label, confirm) => { const button = document.createElement('button'); button.textContent = label; button.onclick = () => finish(confirm); buttons.append(button); };
      if (options.showCancel !== false) add(options.cancelText || '取消', false); add(options.confirmText || '确定', true);
      box.append(heading, content, buttons); mask.append(box); overlays.append(mask);
      activeModals.add(record); document.addEventListener('keydown', trapKeys, true); document.addEventListener('focusin', trapFocus, true);
      requestAnimationFrame(() => (input || buttons.lastChild).focus());
    });
  }
  function ownerElement(owner) { return owner?._element || (owner && owner !== current && owner._fragment) || root; }
  function selectorQuery(owner = current) {
    let selection = '', all = false; const operations = [];
    const query = {in(nextOwner) { owner = nextOwner; return query; }, select(selector) { selection = selector; all = false; return query; }, selectAll(selector) { selection = selector; all = true; return query; },
      boundingClientRect(callback) { const selector = selection, multiple = all; operations.push(() => {
        const elements = [...ownerElement(owner).querySelectorAll(selector)];
        const results = elements.map(el => { const rect = el.getBoundingClientRect(); return {id: el.id, dataset: {...el.dataset}, width: rect.width, height: rect.height, top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom}; });
        const result = multiple ? results : results[0] || null; callback?.(result); return result;
      }); return query; }, fields(options, callback) { return query.boundingClientRect(callback); },
      exec(callback) { requestAnimationFrame(() => { if (!owner?._destroyed) { const results = operations.map(operation => operation()); callback?.(results); } }); return query; }};
    return query;
  }
  function canvasContext(id, owner = current) {
    const el = [...ownerElement(owner).querySelectorAll('canvas')].find(canvas => canvas.id === id || canvas.dataset.canvasId === id);
    if (!el) return new Proxy({draw() {}}, {get: (object, key) => object[key] || (() => {})});
    const rect = el.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    const width = Math.max(rect.width, 1), height = Math.max(rect.height, 1);
    el.width = Math.round(width * ratio); el.height = Math.round(height * ratio);
    const context = el.getContext('2d'); context.scale(ratio, ratio);
    const setters = {draw(reserve, callback) { callback?.(); }, setFillStyle(value) { context.fillStyle = value; }, setStrokeStyle(value) { context.strokeStyle = value; },
      setLineWidth(value) { context.lineWidth = value; }, setLineCap(value) { context.lineCap = value; }, setLineJoin(value) { context.lineJoin = value; },
      setFontSize(value) { context.font = value + 'px -apple-system, sans-serif'; }, setTextAlign(value) { context.textAlign = value; },
      setTextBaseline(value) { context.textBaseline = value; }, setGlobalAlpha(value) { context.globalAlpha = value; },
      setShadow(x, y, blur, color) { Object.assign(context, {shadowOffsetX: x, shadowOffsetY: y, shadowBlur: blur, shadowColor: color}); }};
    return new Proxy(setters, {get: (object, key) => key in object ? object[key] : typeof context[key] === 'function' ? context[key].bind(context) : context[key], set: (object, key, value) => { context[key] = value; return true; }});
  }
  function createEventChannel(owner, events = {}) {
    const listeners = new Map(Object.entries(events).map(([name, fn]) => [name, new Set([fn])]));
    const epoch = navigationEpoch;
    const channel = {
      on(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
      once(name, fn) { const once = (...args) => { channel.off(name, once); fn(...args); }; channel.on(name, once); },
      off(name, fn) { if (fn) listeners.get(name)?.delete(fn); else listeners.delete(name); },
      emit(name, ...args) { if (epoch !== navigationEpoch || owner?._destroyed) return; for (const fn of [...(listeners.get(name) || [])]) fn(...args); }
    };
    return channel;
  }
  function navigation(options, action) { try { const result = {errMsg: 'navigate:ok', ...action()}; options.success?.(result); options.complete?.(result); } catch (error) { options.fail?.({errMsg: error.message}); options.complete?.({errMsg: error.message}); if (!options.fail) report(error); } }
  const wx = {
    navigateTo(options) { navigation(options, () => { const eventChannel = createEventChannel(current, options.events); route(options.url, {eventChannel}); return {eventChannel}; }); }, redirectTo(options) { navigation(options, () => { route(options.url, {replace: true}); }); },
    switchTab(options) { navigation(options, () => route(options.url, {tab: true})); }, reLaunch(options) { navigation(options, () => route(options.url, {reset: true})); },
    navigateBack(options = {}) { navigation(options, () => back(options.delta || 1)); },
    hideTabBar() { hiddenTabs = true; notifyNavigation(); }, showTabBar() { hiddenTabs = false; notifyNavigation(); },
    setNavigationBarTitle(options) { titleOverride = options.title; notifyNavigation(); },
    showToast(options) { toast(options.title || '', options.duration || 2500); }, showModal: modal,
    showLoading(options = {}) { wx.hideLoading(); const el = toast(options.title || '加载中', 120000); el.dataset.loading = 'true'; },
    hideLoading() { overlays.querySelectorAll('[data-loading]').forEach(el => el.remove()); },
    showNavigationBarLoading() {}, hideNavigationBarLoading() {}, stopPullDownRefresh() {}, vibrateShort() {},
    nextTick(callback) { requestAnimationFrame(callback); }, createSelectorQuery: selectorQuery, createCanvasContext: canvasContext,
    pageScrollTo,
    getSystemInfoSync: runtimeWindowInfo,
    env: {USER_DATA_PATH: 'sales-web'}, cloud: {init() {}}
  };
  function resize() {
    if (current && current._contentScroll !== undefined && current._contentScroll !== usesContentScroll()) scrollPageTo(current._scrollY || 0);
    const width = root.clientWidth || window.innerWidth;
    root.style.setProperty('--rpx', Math.min(width / 750, 0.6) + 'px');
    if (current) pageComponentsLifecycle(current, 'resize');
  }
  async function boot() {
    if (!window.SALES_BUNDLE) throw new Error('未加载前端源码包');
    bundle = window.SALES_BUNDLE; bundle.components ||= {}; bundle.images ||= {};
    if (current) hidePage(current); [...new Set([...stack, ...tabPages.values()])].forEach(unload); stack = []; tabPages.clear(); current = null; cache = {}; definitions = {}; componentDefinitions = {};
    if (!window.SalesPlatform || typeof window.SalesPlatform.install !== 'function') throw new Error('未加载 Web 平台适配器');
    window.SalesPlatform.install(wx, {toast, report, windowInfo: runtimeWindowInfo});
    // A browser reload creates a new page instance; replay only its source greeting.
    if (!documentBooted) { wx.removeStorageSync('homeGreetingShownLogin'); documentBooted = true; }
    requireModule('app'); installSessionBoundary(); await lifecycle(app, 'onLaunch'); resize(); lifecycle(app, 'onShow');
    const desired = location.hash.slice(1), first = '/' + (bundle.config.pages && bundle.config.pages[0] || bundle.config.tabBar.list[0].pagePath);
    pendingDeepLink = sessionStorage.getItem('sales-web:pending-path:' + (window.SALES_MODE || 'live')) || '';
    if (!app.globalData.session && !sessionStorage.getItem(sessionKey('signed-out')) && desired && !desired.startsWith('/pages/login/') && bundle.pages[desired.split('?')[0].replace(/^\//, '')]) {
      pendingDeepLink = desired;
      sessionStorage.setItem('sales-web:pending-path:' + (window.SALES_MODE || 'live'), desired);
    }
    if (typeof app.ensureLogin === 'function' && !app.ensureLogin()) return current;
    return route(desired && bundle.pages[desired.split('?')[0].replace(/^\//, '')] ? desired : first, {reset: true});
  }
  window.SalesRuntime = {boot, route, replacePageQuery, userRoute(path, options) { return userNavigation(() => route(path, options)); }, back(delta) { return userNavigation(() => back(delta)); }, signOut() { app.logout(); return route('/pages/login/index', {reset: true}); }, refresh() { const path = current?._url; return path && userNavigation(() => route(path, {replace: true})); },
    businessOptions() { return requireModule('utils/businessOptions'); },
    get app() { return app; }, get current() { return current; }, get wx() { return wx; }, get errors() { return errors.slice(); }};
  window.addEventListener('resize', () => { resize(); const {windowWidth, windowHeight} = wx.getSystemInfoSync(); lifecycle(current, 'onResize', {size: {windowWidth, windowHeight}}); });
  if (window.ResizeObserver) new ResizeObserver(resize).observe(root);
  function onPageScroll(event) {
    if (!current || (usesContentScroll() ? event.target !== pageScrollHost() : event.currentTarget !== window)) return;
    const page = current;
    const {top, height, total} = pageScrollMetrics();
    if (!page._scrollRestore) page._scrollY = top;
    lifecycle(page, 'onPageScroll', {scrollTop: top});
    if (current !== page || page._scrollRestore) return;
    if (total > height + 1 && top + height >= total - 80 && Date.now() - (current._reachBottomAt || 0) > 350) {
      current._reachBottomAt = Date.now(); lifecycle(current, 'onReachBottom');
    }
  }
  root.addEventListener('scroll', onPageScroll, {passive: true, capture: true});
  window.addEventListener('scroll', onPageScroll, {passive: true});
  for (const type of ['wheel', 'touchstart', 'pointerdown']) window.addEventListener(type, event => {
    if (!usesContentScroll() || root.contains(event.target)) cancelScrollRestore();
  }, {passive: true, capture: true});
  window.addEventListener('keydown', event => { if (['Tab', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) cancelScrollRestore(); });
})();
