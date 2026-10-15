// Entry codes choose a login destination; only a server actor grants membership.
// New codes have a narrow format; existing companies may use older codes.
function normalizeCode(value) {
  if (typeof value !== 'string' || /[\u0000-\u001f\u007f-\u009f]/.test(value)) return '';
  const code = value.trim().toLowerCase();
  return code.length >= 1 && code.length <= 128 ? code : '';
}
function validCode(value) { return !!normalizeCode(value); }
function parse(options = {}) {
  const query = options.query && typeof options.query === 'object' ? options.query : options;
  const codes = [];
  if (Object.prototype.hasOwnProperty.call(query, 'company')) codes.push(query.company);
  if (typeof query.scene === 'string') {
    let scene;
    try { scene = decodeURIComponent(query.scene); } catch (_) { return {error:'公司入口无效，请重新扫码或手动填写公司码'}; }
    if (scene.length > 32 || !/^c=[a-zA-Z0-9_.~-]+$/.test(scene)) return {error:'公司入口无效，请重新扫码或手动填写公司码'};
    codes.push(scene.slice(2));
  }
  if (!codes.length) return null;
  const normalized = codes.map(normalizeCode);
  if (normalized.some(code => !code || code !== normalized[0])) return {error:'公司入口无效，请重新扫码或手动填写公司码'};
  return {code:normalized[0]};
}
function fromActor(actor = {}) {
  return validCode(actor.company_code) ? {companyCode:normalizeCode(actor.company_code),
    companyName:typeof actor.company_name === 'string' ? actor.company_name.trim() : ''} : {};
}
function sessionKey(session) {
  return session ? JSON.stringify([session.workspaceId,session.userId,session.account,session.loginAt,session.apiBaseUrl]) : "";
}
module.exports = {validCode,normalizeCode,parse,fromActor,sessionKey};
