const buildInfo = require('../buildInfo.js');

function buildVersion(platform, info = buildInfo) {
  const revision = /^[0-9a-f]{40}$/.test(info.sourceRevision || '') ? info.sourceRevision.slice(0, 7) : '';
  // A LOCAL artifact must never inherit a stale WeChat release label.
  if (info.environment === 'local') return revision ? `LOCAL · ${revision}` : 'LOCAL · 构建信息缺失';
  let miniProgram = {};
  try { miniProgram = platform.getAccountInfoSync().miniProgram || {}; } catch (_) { /* older runtimes */ }
  const version = typeof miniProgram.version === 'string' ? miniProgram.version.trim() : '';
  if (version && ['release', 'trial'].includes(miniProgram.envVersion)) {
    return `${version}${miniProgram.envVersion === 'trial' ? ' · 体验版' : ''}`;
  }
  return revision ? `开发版 · ${revision}` : '开发版';
}

module.exports = { buildVersion };
