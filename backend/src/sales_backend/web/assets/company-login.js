// Login hints are not credentials. Storage is origin-isolated by the browser.
const KEY = 'salesbuddy.console.login-hint.v1';
// A rejected password login is not an expired authenticated session. Keep
// explicit account/company restrictions and non-401 failures unchanged.
export function passwordLoginError(error) {
  if (Number(error?.statusCode) === 401 &&
      (!error.code || ['AUTH_REQUIRED', 'TOKEN_INVALID', 'SESSION_EXPIRED', 'HTTP_401', 'INVALID_CREDENTIALS'].includes(error.code))) {
    return Object.assign(error, {code: 'INVALID_CREDENTIALS', message: '账号或密码错误', recoveryAction: 'correct_request'});
  }
  return error;
}
export function normalizeCompanyCode(value) {
  const code = String(value || '').trim().toLowerCase();
  return code.length >= 1 && code.length <= 128 && !/[\u0000-\u001f\u007f]/.test(code) ? code : '';
}
export function readCompanyEntry(search) {
  const params = new URLSearchParams(search);
  const values = params.getAll('company');
  if (!values.length) return {code: '', invalid: false};
  const code = values.length === 1 ? normalizeCompanyCode(values[0]) : '';
  return {code, invalid: !code};
}
export function readLoginHint(storage) {
  try {
    const data = JSON.parse(storage.getItem(KEY));
    if (data?.remember === false) return {remember: false};
    const company_code = normalizeCompanyCode(data?.company_code);
    if (company_code) return {remember: true, company_code,
      company_name: String(data.company_name || '').slice(0, 200),
      account_code: String(data.account_code || '').slice(0, 200)};
  } catch (_) { /* A disabled or corrupt store must not prevent login. */ }
  return {remember: true};
}
export function saveLoginHint(storage, actor, remember) {
  try {
    if (!remember) {storage.setItem(KEY, JSON.stringify({remember: false})); return;}
    const code = normalizeCompanyCode(actor?.company_code);
    if (!code) return;
    // Only canonical fields returned by successful authentication are saved.
    storage.setItem(KEY, JSON.stringify({remember: true, company_code: code,
      company_name: String(actor.company_name || '').slice(0, 200),
      account_code: String(actor.account_code || '').slice(0, 200)}));
  } catch (_) { /* Login remains available in private/storage-disabled mode. */ }
}
export async function resolveCompany(code, {signal, fetcher = fetch} = {}) {
  const normalized = normalizeCompanyCode(code);
  if (!normalized) throw Error('公司码格式不正确。公司码不是公司名称，请使用管理员提供的公司码或专属链接。');
  let response;
  try {
    response = await fetcher('/api/v1/auth/company-entry?company_code=' + encodeURIComponent(normalized), {
      credentials: 'omit', cache: 'no-store', signal,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error('暂时无法确认公司，请检查网络后重试。');
  }
  if (!response.ok) throw Error(response.status === 429 ? '公司查询过于频繁，请稍后重试。' : '公司入口不可用。公司码不是公司名称，请使用管理员提供的公司码或专属链接。');
  const data = await response.json();
  if (normalizeCompanyCode(data.company_code) !== normalized || !data.company_name) throw Error('公司入口返回异常，请稍后重试。');
  return {company_code: normalized, company_name: String(data.company_name)};
}
