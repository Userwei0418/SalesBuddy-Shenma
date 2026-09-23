const test = require('node:test');
const assert = require('node:assert/strict');
const {draftScope} = require('../miniprogram/utils/draftScope');
test('同名销售及同账号跨租户的未提交拜访草稿相互隔离', () => {
  const base = {workspaceId:'w1', userId:'u1', account:'XS001', userName:'张伟'};
  assert.notEqual(draftScope(base), draftScope({...base, userId:'u2'}));
  assert.notEqual(draftScope(base), draftScope({...base, workspaceId:'w2'}));
  assert.equal(draftScope(base), draftScope({...base, userName:'张伟改名'}));
});
