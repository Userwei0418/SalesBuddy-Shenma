import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {clientExportAllowed, collectFollowups, clearCache, defaultScope, filterVisitsToScope, loadDirectory, normalizeVisit, readScopeFor} from '../department-ui/pc/data.js';
import {candidateOptions} from '../scripts/start_candidate.mjs';

const pickerModule = {exports: {}};
new Function('module', readFileSync(new URL('../source/miniprogram/utils/personPicker.js', import.meta.url), 'utf8'))(pickerModule);
const picker = pickerModule.exports;
const range = {kind: 'biweek', start: '2026-09-28', end: '2026-10-11', today: '2026-10-08'};
function runtime(session, api, access = {}) {
  globalThis.SalesRuntime = {app: {globalData: {session}, can: action => session.permissions?.[action] === true},
    require: name => name === 'utils/access' ? access : name === 'utils/personPicker' ? picker : api};
}

test('resource selection delegates to effective grants without deriving scope from role or data_scope', () => {
  const previous = globalThis.SalesRuntime, calls = [];
  try {
    const actor = {role: 'manager', dataScope: 'workspace', teamIds: ['untrusted']};
    runtime(actor, {}, {scopeFor: (session, action) => { calls.push([session, action]); return {allowed: true, scope: 'self', scopes: ['self'], teamIds: []}; }});
    assert.deepEqual(defaultScope(actor, null, 'visit.read'), {kind: 'self'});
    assert.equal(readScopeFor('task.read').scope, 'self');
    assert.deepEqual(calls.map(call => call[1]), ['visit.read', 'task.read']);
    runtime(actor, {});
    assert.throws(() => readScopeFor(), /共享权限模块尚未就绪/);
  } finally { globalThis.SalesRuntime = previous; }
});

test('directory uses the requested resource and reports failure without synthesizing a personal directory', async () => {
  const previous = globalThis.SalesRuntime, calls = [];
  try {
    runtime({userId: 'me', permissionVersion: 'v1'}, {getDirectoryMembers: async params => {calls.push(params); return {items: [{id: 'former', name: '成员', team_ids: ['t2']}], teams: [{id: 't2', name: '部门二'}], defaults: {team_id: 'invisible'}};}});
    const directory = await loadDirectory('task.read');
    assert.deepEqual(calls, [{permission: 'task.read', include_stats: false}]);
    assert.equal(directory.permission, 'task.read');
    assert.deepEqual(directory.members.map(row => row.id), ['former']);
    assert.equal(directory.defaultTeamId, '');
    runtime({userId: 'me'}, {getDirectoryMembers: async () => {throw Object.assign(Error('没有该资源权限'), {statusCode: 403});}});
    await assert.rejects(loadDirectory(), /没有该资源权限/);
    runtime({userId: 'me'}, {getDirectoryMembers: async () => ({items: []})});
    await assert.rejects(loadDirectory(), /人员目录/);
  } finally { globalThis.SalesRuntime = previous; }
});

test('sales and FDE retain server-authorized historical records even when the recorder left the current directory', async () => {
  const previous = globalThis.SalesRuntime;
  try {
    for (const role of ['sales', 'fde', 'supervisor', 'fde_lead']) {
      const calls = [];
      runtime({userId: 'me', role, permissionVersion: role}, {listVisitRange: async params => {calls.push(params); return {items: [{id: 'history', recorder_id: 'former', recorder_team_id: 'historic-team', visit_date: '2026-10-01', status: 'archived'}], has_more: false};}, getFdeActivity: () => {throw Error('不得改用角色范围');}});
      clearCache();
      const result = await collectFollowups({range, scope: {kind: 'team', teamId: 'all'}, directory: {members: [], people: new Map()}});
      assert.deepEqual(result.items.map(row => row.id), ['history']);
      assert.equal(calls[0].date_from, '2026-09-28');
      assert.equal(calls[0].date_to, '2026-10-08');
      assert.equal('recorder_ids' in calls[0], false);
      assert.equal('team_id' in calls[0], false);
    }
    const historical = normalizeVisit({id: 'h', recorder_id: 'm', recorder_team_id: 'old'}, new Map([['m', {team_ids: ['new']}]]));
    assert.equal(historical.teamId, 'old');
    assert.equal(filterVisitsToScope([historical], {kind: 'team', teamId: 'old'}, {members: []}).length, 1);
  } finally { globalThis.SalesRuntime = previous; clearCache(); }
});

test('forbidden range reads never fall back through another resource', async () => {
  const previous = globalThis.SalesRuntime;
  try {
    runtime({userId: 'me', role: 'fde'}, {listVisitRange: async () => {throw Object.assign(Error('跟进已撤权'), {statusCode: 403});}, getFdeActivity: () => {throw Error('错误回退');}, getCustomerMap: () => {throw Error('错误回退');}});
    clearCache();
    await assert.rejects(collectFollowups({range, scope: {kind: 'self'}, directory: {people: new Map()}}), /跟进已撤权/);
  } finally { globalThis.SalesRuntime = previous; clearCache(); }
});

test('local export needs both the independent action and the server whole-page export decision', () => {
  const previous = globalThis.SalesRuntime;
  try {
    const actor = {permissions: {'visit.read': true, 'visit.export': true}, clientExportPermissions: {'visit.export': false}};
    runtime(actor, {});
    assert.equal(clientExportAllowed('visit.export'), false);
    actor.clientExportPermissions['visit.export'] = true;
    assert.equal(clientExportAllowed('visit.export'), true);
    actor.permissions['visit.export'] = false;
    assert.equal(clientExportAllowed('visit.export'), false);
    assert.equal(clientExportAllowed('report.export'), false);
    delete actor.clientExportPermissions;
    assert.equal(clientExportAllowed('visit.export'), false);
  } finally { globalThis.SalesRuntime = previous; }
});

test('candidate server cannot overwrite or proxy the existing acceptance environment', () => {
  assert.equal(candidateOptions().target, 'http://127.0.0.1:18810/api/v1');
  assert.match(candidateOptions().staticDir, /\.runtime\/auth001-dist$/);
  assert.throws(() => candidateOptions({apiPort: 18800}), /独立本地端口/);
  assert.throws(() => candidateOptions({output: 'dist'}), /独立构建目录/);
  assert.throws(() => candidateOptions({output: 'source/miniprogram'}), /Candidate output/);
});
