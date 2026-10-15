const test = require('node:test');
const assert = require('node:assert/strict');
const { buildVersion } = require('../miniprogram/utils/buildVersion');
test('微信运行时使用 JavaScript 构建模块，审计 JSON 与其一致', () => {
  assert.deepEqual(require('../miniprogram/buildInfo.js'),require('../miniprogram/buildInfo.json'));
});
const sourceRevision = '1234567890abcdef1234567890abcdef12345678';
const wx = (envVersion, version) => ({getAccountInfoSync: () => ({miniProgram:{envVersion,version}})});

test('LOCAL 显示固定源码版本，不继承微信缓存版本或未来目标版本', () => {
  for (const env of ['develop','trial','release']) {
    assert.equal(buildVersion(wx(env,'1.1.1'),{environment:'local',sourceRevision}), 'LOCAL · 1234567');
  }
  assert.equal(buildVersion(wx('develop','1.1.1'),{environment:'local',sourceRevision:'invalid'}), 'LOCAL · 构建信息缺失');
});
test('微信正式与体验版本来自当前运行 API，开发环境忽略旧版本字段', () => {
  assert.equal(buildVersion(wx('release','1.1.2')), '1.1.2');
  assert.equal(buildVersion(wx('trial','1.1.2')), '1.1.2 · 体验版');
  assert.equal(buildVersion(wx('develop','1.1.1')), '开发版');
  assert.equal(buildVersion(wx('develop',''),{environment:'online',sourceRevision}), '开发版 · 1234567');
});
test('旧运行时或未打包源码不虚构发布版本', () => {
  assert.equal(buildVersion({}), '开发版');
  assert.equal(buildVersion({getAccountInfoSync(){throw Error('unavailable');}}), '开发版');
  assert.equal(buildVersion(wx('release','')), '开发版');
});
