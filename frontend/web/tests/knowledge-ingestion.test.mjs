import test from 'node:test';
import assert from 'node:assert/strict';
import {assertIngestion, ingestionErrorText, ingestionPresentation, requestIngestion} from '../department-ui/knowledge/ingestion.js';
import {createKnowledgeService} from '../department-ui/knowledge/service.js';
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const context = {material_id: id(4), version_id: id(5), expected_material_revision: 2};
const view = (values = {}) => ({material_id: id(4), version_id: id(5), ingestion_id: null, state: 'saved', availability: 'ready', can_retry: true, error_code: null, processed_files: 0, total_files: 1, observed_at: null, ...values});
const detail = () => ({material_id: id(4), material_revision: 2, capabilities: {read: true, edit: false, manage: false}, versions: [{version_id: id(5), state: 'saved', contribution: {selection: {content: {file_ids: [id(6)], links: []}}}}]});

test('ingestion transport uses the exact version endpoint, revision and explicit idempotency key, with fresh reads', async () => {
  const calls = [], service = createKnowledgeService({request: async options => {calls.push(options); return view();}});
  await service.getIngestion(id(4), id(5));
  await service.startIngestion(id(4), id(5), {expected_material_revision: 2, forged_permission: true}, id(8));
  assert.deepEqual(calls, [
    {fresh: true, path: `/knowledge/materials/${id(4)}/versions/${id(5)}/ingestion`},
    {fresh: true, path: `/knowledge/materials/${id(4)}/versions/${id(5)}/ingestion`, method: 'POST', data: {expected_material_revision: 2}, idempotencyKey: id(8)},
  ]);
  await assert.rejects(service.startIngestion(id(4), id(5), {expected_material_revision: 0}, id(8)), /材料版本/);
  assert.equal(calls.length, 2);
});

test('malformed or different-version responses cannot grant a retry or report success', () => {
  for (const invalid of [{version_id: id(9)}, {material_id: id(9)}, {can_retry: 'true'}, {availability: 'future'}, {state: 'future'}, {processed_files: 2}, {total_files: -1}, {ingestion_id: 'raw provider id'}, {observed_at: 'not a date'}]) {
    assert.throws(() => assertIngestion(view(invalid), id(4), id(5)), /不匹配/);
  }
});

test('unconfigured, links, unknown and stale do not fabricate failure or offer unsafe retry', () => {
  assert.equal(ingestionPresentation(view({availability: 'unconfigured', can_retry: false})).title, '待解析');
  assert.match(ingestionPresentation(view({availability: 'links_not_archived', can_retry: false})).description, /不会自动抓取/);
  for (const availability of ['unconfigured', 'files_required', 'links_not_archived', 'running', 'unknown', 'complete', 'stale']) assert.equal(ingestionPresentation(view({availability})).canRetry, false);
  assert.equal(ingestionPresentation(view({can_retry: false})).canRetry, false);
  assert.equal(ingestionPresentation(view({state: 'failed', availability: 'retryable'})).button, '重试解析');
  assert.equal(ingestionPresentation(view({state: 'retrievable', availability: 'complete'})).title, '可检索');
  assert.equal(ingestionPresentation(view({state: 'deleted'})).canRetry, false);
});

test('explicit ingestion rechecks current version and server permission, independent of manage/edit flags', async () => {
  const calls = [], service = {getMaterial: async () => {calls.push('detail'); return detail();}, getIngestion: async () => {calls.push('state'); return view();}, startIngestion: async (...args) => {calls.push(args); return view({ingestion_id: id(7), state: 'syncing', availability: 'running', can_retry: false});}};
  const result = await requestIngestion(service, context, id(8));
  assert.equal(result.state, 'syncing'); assert.deepEqual(calls, ['detail', 'state', [id(4), id(5), {expected_material_revision: 2}, id(8)]]);
});

test('revision, legacy, authorization and identity changes stop before any POST', async () => {
  let posts = 0;
  const service = {getMaterial: async () => detail(), getIngestion: async () => view(), startIngestion: async () => {posts++; return view();}};
  await assert.rejects(requestIngestion({...service, getMaterial: async () => ({...detail(), material_revision: 3})}, context, id(8)), /版本已变化/);
  await assert.rejects(requestIngestion({...service, getMaterial: async () => ({...detail(), versions: [{version_id: id(5), contribution: {kind: 'legacy_import', source: {provider: 'feishu_base'}}}]})}, context, id(8)), /版本已变化/);
  for (const status of [view({can_retry: false}), view({availability: 'unknown'}), view({availability: 'running'})]) await assert.rejects(requestIngestion({...service, getIngestion: async () => status}, context, id(8)), /不允许/);
  let current = true;
  await assert.rejects(requestIngestion({...service, getIngestion: async () => {current = false; return view();}}, context, id(8), () => current), /身份/);
  assert.equal(posts, 0);
});

test('unknown POST outcomes are not retried automatically and never expose raw provider content', async () => {
  let posts = 0;
  const service = createKnowledgeService({request: async options => {
    if (options.method === 'POST') {posts++; throw Object.assign(Error('https://private-provider/?signed-secret'), {statusCode: 503, serverCode: 'UNKNOWN_PROVIDER_ERROR'});}
    return options.path.endsWith('/ingestion') ? view() : {...detail(), can_edit: false, can_manage: false};
  }});
  await assert.rejects(requestIngestion(service, context, id(8)), error => {assert.match(error.message, /不会自动重复提交/); assert.equal(error.message.includes('private-provider'), false); return true;});
  assert.equal(posts, 1);
  assert.equal(ingestionErrorText({statusCode: 403, message: 'secret'}).includes('secret'), false);
});
