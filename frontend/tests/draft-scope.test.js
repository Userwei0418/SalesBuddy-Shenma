const test = require('node:test');
const assert = require('node:assert/strict');
const {draftScope} = require('../miniprogram/utils/draftScope');
test('同名销售及同账号跨租户的未提交拜访草稿相互隔离', () => {
  const base = {workspaceId:'w1', userId:'u1', account:'XS001', userName:'张伟'};
  assert.notEqual(draftScope(base), draftScope({...base, userId:'u2'}));
  assert.notEqual(draftScope(base), draftScope({...base, workspaceId:'w2'}));
  assert.equal(draftScope(base), draftScope({...base, userName:'张伟改名'}));
});
test('退出登录的空身份可比较，不与任何账号草稿共用范围',()=>{
  assert.equal(draftScope(null),draftScope());
  assert.notEqual(draftScope(null),draftScope({workspaceId:'w',userId:'u',role:'sales'}));
});
