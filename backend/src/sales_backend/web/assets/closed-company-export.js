const API = '/api/v1/closed-company-export';
const STALE = 'EXPORT_SESSION_CHANGED';

export function createClosedCompanyExportPage({document, fetch, URL, setTimeout, clearTimeout, FormData = globalThis.FormData, now = Date.now}) {
  const $ = id => document.getElementById(id);
  let session = null, options = null, generation = 0, optionsRequest = 0, expiryTimer, busy = false, loggingOut = false;
  const pending = new Set(), downloads = new Map();
  function notice(message = '', error = false) {
    $('notice').textContent = message;
    $('notice').classList.toggle('error', error);
  }
  function cancelRequests() { for (const controller of pending) controller.abort(); pending.clear(); }
  function clearDownloads() {
    for (const [url, timer] of downloads) { clearTimeout(timer); URL.revokeObjectURL(url); }
    downloads.clear();
  }
  function signedOut(message = '', error = false) {
    generation += 1;
    session = null; options = null; busy = false;
    clearTimeout(expiryTimer); cancelRequests(); clearDownloads();
    $('login-password').value = '';
    $('export-form').reset(); $('sections').replaceChildren();
    for (const id of ['company-name', 'company-code', 'account-name', 'session-expiry', 'export-note', 'capacity']) $(id).textContent = '';
    $('export-panel').hidden = true; $('export-form').hidden = true; $('logout').hidden = true;
    $('logout').textContent = '退出登录';
    $('login-panel').hidden = false; $('login-submit').disabled = loggingOut;
    $('export-fields').disabled = false;
    notice(message, error);
  }
  function checkCurrent(expected) {
    if (expected !== generation) throw Object.assign(Error('导出身份已变化，请重新操作'), {code: STALE});
    if (session && now() >= Date.parse(session.expires_at)) {
      signedOut('导出会话已到期，请重新登录。', true);
      throw Object.assign(Error('导出会话已到期，请重新登录。'), {code: STALE});
    }
  }
  function failure(error) { if (error.code !== STALE) notice(error.message || '操作未完成，请重试。', true); }
  function errorMessage(status, data) {
    const detail = typeof data?.detail === 'string' ? data.detail : '';
    if (detail && !/^[A-Z][A-Z0-9_]+$/.test(detail)) return detail.slice(0, 500);
    return ({401:'登录验证失败或会话已过期，请重新登录。',403:'当前账号没有此项导出权限，请联系公司管理员。',409:'当前状态不允许导出或原件不可用，请核对后重试。',413:'资料包超出容量限制，请减少分类或取消原件后重试。',422:'请检查公司码、账号或导出内容是否填写完整。',429:'请求较多或已有导出正在处理，请稍后重试。',504:'生成资料包超时，请减少分类后重试。'})[status] || '服务暂时不可用，请稍后重试。';
  }
  async function request(path, {method = 'GET', body, binary = false} = {}, expected = generation) {
    checkCurrent(expected);
    const controller = new AbortController(); pending.add(controller);
    let timedOut = false;
    const timeoutSeconds = binary ? Math.min(600, Math.max(1, Number(options?.timeout_seconds) || 180)) + 30 : 30;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutSeconds * 1000);
    try {
      const response = await fetch(API + path, {method, credentials:'same-origin', cache:'no-store', signal:controller.signal,
        headers:body ? {'Content-Type':'application/json'} : {}, ...(body ? {body:JSON.stringify(body)} : {})});
      checkCurrent(expected);
      if (!response.ok) {
        let data; try { data = await response.json(); } catch (_) { data = null; }
        checkCurrent(expected);
        const initialLogin = response.status === 401 && path === '/session';
        const message = initialLogin ? '请使用获授权的公司管理员账号登录。' : errorMessage(response.status, data);
        if (response.status === 401) signedOut(message, !initialLogin);
        throw Error(message);
      }
      if (binary) {
        const length = Number(response.headers.get('Content-Length'));
        if (!/^application\/zip(?:;|$)/i.test(response.headers.get('Content-Type') || '') || !Number.isSafeInteger(length) || length <= 0 || length > options.max_bytes) {
          throw Error('资料包响应不完整或超出容量限制，未开始保存，请重试。');
        }
        const blob = await response.blob();
        checkCurrent(expected);
        if (blob.size !== length) throw Error('资料包传输不完整，未开始保存，请重新导出。');
        return blob;
      }
      const data = response.status === 204 ? null : await response.json();
      checkCurrent(expected); return data;
    } catch (error) {
      checkCurrent(expected);
      if (timedOut) throw Error('请求超时，操作未完成，请稍后重试。');
      if (error.name === 'TypeError') throw Error('网络连接中断，操作未完成，请检查网络后重试。');
      if (error.name === 'SyntaxError') throw Error('服务响应不完整，请重试或联系管理员。');
      throw error;
    } finally { clearTimeout(timeout); pending.delete(controller); }
  }
  function setSession(value) {
    if (!value || !['workspace_id','company_name','company_code','account_code','display_name'].every(key => typeof value[key] === 'string' && value[key].trim()) || !Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= now()) {
      signedOut('导出会话无效或已到期，请重新登录。', true); return false;
    }
    session = value;
    $('login-panel').hidden = true; $('export-panel').hidden = false; $('logout').hidden = false;
    $('company-name').textContent = value.company_name;
    $('company-code').textContent = '公司码：' + value.company_code;
    $('account-name').textContent = value.display_name + ' · ' + value.account_code;
    $('session-expiry').textContent = '本次会话到期：' + new Date(value.expires_at).toLocaleString('zh-CN', {hour12:false});
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(() => signedOut('导出会话已到期，请重新登录。', true), Math.max(0, Date.parse(value.expires_at) - now()));
    return true;
  }
  function updateOriginals() {
    const visits = [...$('sections').querySelectorAll('input:checked')].some(input => input.value === 'visits');
    $('include-originals').disabled = !options?.originals_available || !visits;
    if ($('include-originals').disabled) $('include-originals').checked = false;
    $('originals-note').textContent = !options?.originals_available ? '当前账号没有导出原件的权限。' : visits ? '原件会随跟进记录一同打包。' : '如需原件，请先选择“跟进记录”。';
  }
  async function loadOptions() {
    const expected = generation, requestId = ++optionsRequest;
    $('retry-options').hidden = true; $('export-form').hidden = true; options = null;
    notice('正在读取本公司的导出范围…');
    try {
      const data = await request('/options', {}, expected);
      if (requestId !== optionsRequest) return;
      if (!Array.isArray(data?.sections) || !data.sections.length || data.sections.length > 8 || !Number.isSafeInteger(data.max_bytes) || data.max_bytes <= 0 || !Number.isSafeInteger(data.max_records) || data.max_records <= 0 || typeof data.originals_available !== 'boolean' || data.sections.some(item => !/^[a-z_]+$/.test(item.code) || typeof item.label !== 'string' || typeof item.available !== 'boolean') || new Set(data.sections.map(item => item.code)).size !== data.sections.length) {
        throw Error('导出选项不完整，请重新加载或联系管理员。');
      }
      options = data; $('sections').replaceChildren();
      for (const item of data.sections) {
        const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
        input.type = 'checkbox'; input.name = 'sections'; input.value = item.code;
        input.checked = item.available; input.disabled = !item.available;
        input.addEventListener('change', updateOriginals);
        text.textContent = item.label + (item.available ? '' : '（无公司级权限）');
        label.append(input, text); $('sections').append(label);
      }
      $('export-note').textContent = typeof data.note === 'string' ? data.note : '';
      $('capacity').textContent = `单包最多 ${(data.max_bytes / 1048576).toLocaleString('zh-CN', {maximumFractionDigits:2})} MiB、${data.max_records.toLocaleString('zh-CN')} 条记录。`;
      $('export-form').hidden = false; updateOriginals(); notice();
    } catch (error) {
      if (requestId !== optionsRequest) return;
      if (expected === generation && session) $('retry-options').hidden = false;
      failure(error);
    }
  }
  async function login(event) {
    event.preventDefault(); if (busy || loggingOut) return;
    busy = true; $('login-submit').disabled = true;
    const fields = new FormData($('login-form'));
    const body = {company_code:String(fields.get('company_code') || '').trim(), account_code:String(fields.get('account_code') || '').trim(), password:String(fields.get('password') || '')};
    $('login-password').value = ''; notice('正在验证身份…');
    try {
      const value = await request('/login', {method:'POST', body});
      if (setSession(value)) await loadOptions();
    } catch (error) { failure(error); }
    finally { busy = false; $('login-submit').disabled = loggingOut; }
  }
  async function exportData(event) {
    event.preventDefault(); if (busy || !session || !options) return;
    const expected = generation, fields = new FormData($('export-form'));
    const sections = fields.getAll('sections'), reason = String(fields.get('reason') || '').trim(), include_originals = fields.has('include_originals');
    if (!sections.length) return notice('请选择至少一个资料分类。', true);
    if (reason.length < 3 || reason.length > 200) return notice('请填写 3–200 字的导出用途。', true);
    if (include_originals && (!options.originals_available || !sections.includes('visits'))) return notice('导出原件需具备权限，并同时选择跟进记录。', true);
    busy = true; $('export-fields').disabled = true;
    notice('正在生成并接收资料包，请保持页面打开…');
    try {
      const blob = await request('/export', {method:'POST', binary:true, body:{sections, include_originals, reason}}, expected);
      checkCurrent(expected);
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = 'SalesBuddy-' + session.company_code.replace(/[^a-zA-Z0-9_-]/g, '_') + '-历史资料.zip';
      document.body.append(anchor); anchor.click(); anchor.remove();
      downloads.set(url, setTimeout(() => { URL.revokeObjectURL(url); downloads.delete(url); }, 60000));
      notice('资料包已生成，已请求浏览器保存。请在下载列表确认文件完整；本页面无法确认文件是否已保存到设备。');
    } catch (error) { failure(error); }
    finally { if (expected === generation) { busy = false; $('export-fields').disabled = false; } }
  }
  async function logout() {
    if (loggingOut) return;
    loggingOut = true; signedOut('正在退出导出会话…');
    try { await request('/logout', {method:'POST'}); $('logout').hidden = true; notice('已退出导出会话。'); }
    catch (error) {
      if (error.code !== STALE) {
        $('logout').hidden = false; $('logout').textContent = '重试退出';
        notice('页面已清空，但服务端退出尚未确认。' + error.message + ' 可点击“重试退出”。', true);
      }
    }
    finally { loggingOut = false; $('login-submit').disabled = false; }
  }
  async function start() {
    $('login-form').addEventListener('submit', login);
    $('export-form').addEventListener('submit', exportData);
    $('logout').addEventListener('click', logout);
    $('retry-options').addEventListener('click', loadOptions);
    document.addEventListener('visibilitychange', () => { if (session) { try { checkCurrent(generation); } catch (_) {} } });
    try { const value = await request('/session'); if (setSession(value)) await loadOptions(); }
    catch (error) { if (error.code !== STALE) signedOut(error.message, true); }
  }
  return {start};
}

if (typeof document !== 'undefined') createClosedCompanyExportPage({document, fetch:globalThis.fetch.bind(globalThis), URL:globalThis.URL, setTimeout:globalThis.setTimeout.bind(globalThis), clearTimeout:globalThis.clearTimeout.bind(globalThis)}).start();
