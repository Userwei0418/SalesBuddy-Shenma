// Explicit opt-in credentials, scoped to account and API on this device.
// Local storage is not a secure vault. Never log or export these records.
const KEY = 'salesRememberedLoginV1';
const normalizeAccount = value => String(value || '').trim().toUpperCase();
function valid(value) {
  return value && typeof value.baseUrl === 'string' && value.baseUrl &&
    typeof value.account === 'string' && normalizeAccount(value.account) &&
    typeof value.password === 'string' && value.password && value.password.length <= 128;
}
function records() {
  const value = wx.getStorageSync(KEY);
  if (!value) return [];
  // Read the previous single-account format without losing its opted-in entry.
  if (value.version === 1) return valid(value) ? [value] : [];
  if (value.version !== 2 || !Array.isArray(value.entries)) return [];
  return value.entries.filter(valid);
}
function matches(value, baseUrl, account) {
  return value.baseUrl === baseUrl && normalizeAccount(value.account) === normalizeAccount(account);
}
function read(baseUrl, account) {
  try {
    const entries = records().filter(value => value.baseUrl === baseUrl &&
      (account === undefined || matches(value,baseUrl,account)));
    const value = entries[entries.length - 1];
    return value ? {account:value.account,password:value.password} : null;
  } catch (_) { return null; }
}
function suggest(baseUrl, prefix) {
  const query = normalizeAccount(prefix);
  if (!query) return [];
  try {
    const seen = new Set();
    return records().slice().reverse().filter(value => {
      const account = normalizeAccount(value.account);
      if (value.baseUrl !== baseUrl || account === query || !account.startsWith(query) || seen.has(account)) return false;
      seen.add(account); return true;
    }).slice(0,5).map(value => value.account);
  } catch (_) { return []; }
}
function clear(baseUrl, account) {
  const entries = records().filter(value => !matches(value,baseUrl,account));
  if (entries.length) wx.setStorageSync(KEY,{version:2,entries});
  else wx.removeStorageSync(KEY);
}
function save(baseUrl, account, password) {
  const value = {baseUrl,account:account.trim(),password};
  if (!valid(value)) return;
  const entries = records().filter(item => !matches(item,baseUrl,account));
  // Most recently saved account is the default when opening a fresh login page.
  entries.push(value);
  wx.setStorageSync(KEY,{version:2,entries});
}
module.exports = {read, clear, save, suggest, normalizeAccount};
