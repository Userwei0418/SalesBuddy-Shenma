import test from 'node:test';
import assert from 'node:assert/strict';
import {auditClient, contract, checkRequest, validate} from '../scripts/check-v3-contract.mjs';
import {compareSnapshots} from '../scripts/compare-v3-openapi.mjs';
const ID = '00000001-0000-4000-8000-000000000001';
const request = (path, method = 'GET', data) => ({url: 'https://offline.invalid/api/v1' + path, method, data});

test('current core client requests match the supplied 1.0.6/V119 contract without network access', async () => {
  const report = await auditClient(); assert.equal(report.realRequests, 0); assert.deepEqual(report.errors, []);
  assert.equal(report.baseline, '6b8aa7a8a5e5bed99341551986f3525df54197dc');
  assert.equal(report.coveredOperationCount, 81);
  for (const op of ['POST /api/v1/customers/{customer_id}/claims', 'POST /api/v1/tasks/{task_id}/events', 'POST /api/v1/visits', 'PATCH /api/v1/visits/{visit_id}', 'GET /api/v1/directory/colleagues']) assert.ok(report.results.some(r => r.operation === op));
  // Optional MutationKey and undocumented parameters are risks, not fabricated 422 failures.
  assert.deepEqual([...new Set(report.warnings.map(w=>w.operation))].sort(), ['POST /api/v1/conversations','POST /api/v1/fde/profile/review','POST /api/v1/targets/batch','POST /api/v1/visit-flow/quality','POST /api/v1/visit-flow/structure']);
  assert.deepEqual(report.semantics, {opportunityAmountYuan: 123456, recognizedForecastYuan: 0, collectionForecastYuan: 20000,
    actualAmountYuan: '123.45', actualRequestIdPresent: true, opportunityVersionOmittedOnCreate: true, taskEventVersion: 3, taskCompletionEvents:[{event_type:'complete',version_no:3},{event_type:'approve_completion',version_no:4},{event_type:'reject_completion',version_no:4}], metadataRequestCount:1, teamDirectoryPurposes:['browse','dashboard','profile','fde','assignment'], dueAtUTC: '2026-12-31T02:00:00.000Z'});
});

test('validator detects wrong version path, method, identifiers and missing required filters', () => {
  assert.equal(checkRequest({url: 'https://offline.invalid/api/v3/tasks', method: 'GET'}).operation, null);
  assert.equal(checkRequest(request('/customers/' + ID + '/claims', 'PUT')).operation, null);
  assert.ok(checkRequest(request('/tasks/not-a-uuid')).errors.length);
  assert.ok(checkRequest(request('/visits?page_size=20')).errors.some(e => e.includes('customer_id')));
  assert.ok(checkRequest(request('/customers/claim-pool?page_size=101')).errors.length);
  assert.ok(checkRequest(request('/opportunities?scope=company')).errors.length);
  assert.ok(checkRequest(request('/customer-assets?as_of=2026-09-14')).warnings.some(w => w.includes('as_of')), 'as_of is documented for quarters, not the entries endpoint');
});

test('task association kinds and legacy server position codes retain explicit version semantics', () => {
  const base = {description: '离线合法任务样本', due_at: '2026-12-31T10:00:00+08:00'};
  assert.equal(validate({...base, target_position: 'self'}, contract.schemas.TaskCreate).length, 0);
  assert.ok(validate({...base, target_position: 'sales'}, contract.schemas.TaskCreate).length);
  assert.equal(validate({...base, association_kind:'customer'}, contract.schemas.TaskCreate).length,0);
  assert.ok(validate({...base, association_kind:'unknown'}, contract.schemas.TaskCreate).length);
  assert.ok(validate({event_type: 'delete'}, contract.schemas.TaskEventCreate).length);
  // Optional in OpenAPI is not a claim that concurrent business writes need no version guard.
  assert.equal(validate({event_type: 'accept'}, contract.schemas.TaskEventCreate).length, 0);
  assert.ok(validate({event_type: 'accept', version_no: -1}, contract.schemas.TaskEventCreate).length);
});

test('MutationKey support is distinguished from mandatory headers and opaque business guards', () => {
  const row = checkRequest(request('/conversations', 'POST', {mode: 'visit_entry', customer_id: ID}));
  assert.equal(row.errors.length, 0); assert.equal(row.mutationKey, true); assert.equal(row.warnings.length, 1);
  const malformed = {...request('/conversations', 'POST', {mode: 'visit_entry'}), header: {'Idempotency-Key': 'not-a-uuid'}};
  assert.ok(checkRequest(malformed).errors.length);
  assert.ok(validate({mode: 'task_create'}, contract.schemas.ConversationCreate).length);
  assert.equal(validate({mode: 'management_task'}, contract.schemas.ConversationCreate).length, 0);
  assert.equal(checkRequest(request('/console/claims/' + ID + '/decision', 'POST', {decision: 'approved', reason: '离线契约字段'})).errors.length, 0);
  assert.ok(validate({decision: 'pending'}, contract.schemas.ClaimDecision).length);
});

test('pagination distinguishes company directory totals, detail cursors and absent versus zero money', () => {
  assert.ok(validate({items: [], has_more: false}, contract.schemas.CustomerDirectoryPage).length, 'directory total is required');
  assert.equal(validate({items: [], total: 0, has_more: false, next_offset: null}, contract.schemas.CustomerDirectoryPage).length, 0);
  assert.equal(validate({items: [], has_more: true, next_offset: null, next_cursor: 'opaque'}, contract.schemas.VisitHistoryPage).length, 0);
  assert.ok(validate({items: [], has_more: false}, contract.schemas.VisitHistoryPage).length, 'detail next_offset is required');
  assert.equal(validate({year: 2026, quarter: 4, recognized_amount: 0, collection_amount: null}, contract.schemas.QuarterForecast).length, 0);
  assert.ok(validate({amount: '123.45', kind: 'recognized', customer_id: ID, source_ref: 'OFFLINE', occurred_on: '2026-09-14', confirmed: true}, contract.schemas.ActualCreate).length, 'business request_id must be present independently of transport header');
});

test('semantic comparison ignores prose/order but preserves schema business-field names and constraints', () => {
  const body = {type: 'object', title: 'Documentation title', description: 'Old prose', required: ['title', 'summary'], properties: {title: {type: 'string'}, summary: {type: 'string'}, status: {enum: ['pending', 'done'], default: 'pending'}}};
  const pack = {baseline: 'test', operations: [{method: 'POST', path: '/api/v1/example', parameters: [], responses: {'200': {description: 'OK', content: {'application/json': {schema: body}}}}}], schemas: {Record: body}};
  const live = {paths: {'/api/v1/example': {post: {responses: structuredClone(pack.operations[0].responses)}}}, components: {schemas: {Record: structuredClone(body)}}};
  live.components.schemas.Record.description = 'Changed prose'; live.components.schemas.Record.required.reverse(); live.components.schemas.Record.properties.status.enum.reverse();
  assert.equal(compareSnapshots(pack, live).equivalent, true);
  live.components.schemas.Record.properties.title.type = 'integer';
  const changed = compareSnapshots(pack, live); assert.equal(changed.equivalent, false); assert.equal(changed.changedSchemas[0].changes[0].field, 'properties.title.type');
});

test('semantic comparison detects removed operations and schema defaults', () => {
  const pack = {baseline: 'test', operations: [{method: 'GET', path: '/api/v1/example', parameters: [], responses: {'200': {}}}], schemas: {Record: {type: 'object', default: {title: 'before'}}}};
  const live = {paths: {}, components: {schemas: {Record: {type: 'object', default: {title: 'after'}}}}};
  const result = compareSnapshots(pack, live); assert.equal(result.equivalent, false);
  assert.deepEqual(result.missingOperations, ['GET /api/v1/example']); assert.equal(result.changedSchemas[0].changes[0].field, 'default.title');
});

// New server-owned catalog, stable team IDs and completion review are checked
// against the supplied package, not a live server or an inferred role model.
test('1.0.6 metadata and team directory are declared and retain stable UUID identities', () => {
  assert.equal(contract.database_version, 'V119');
  for (const path of ['/metadata/business-options', '/directory/teams?purpose=browse', '/directory/teams?purpose=assignment']) {
    const checked=checkRequest(request(path)); assert.ok(checked.operation); assert.deepEqual(checked.errors,[]);
  }
  assert.ok(checkRequest(request('/directory/teams?purpose=unknown')).errors.length);
  assert.deepEqual(validate({data_source:'database',teams:[{id:ID,name:'有权限的空团队',parent_id:null}]},contract.schemas.TeamDirectory),[]);
  assert.ok(validate({data_source:'database',teams:[{id:'team-name',name:'不能用名称冒充ID'}]},contract.schemas.TeamDirectory).length);
  assert.deepEqual(checkRequest(request('/opportunities?team_id='+ID)).errors,[]);
  assert.ok(checkRequest(request('/opportunities?team_id=team-name')).errors.length);
  assert.equal(contract.offline_business_options_fixture.contract_version,1);
  assert.ok(contract.offline_business_options_fixture.opportunity.stages.every(row=>['open','won','lost'].includes(row.status)));
});

test('completion review events are supported with version guards and completed/pending-review filters', () => {
  for (const event_type of ['complete','approve_completion','reject_completion']) {
    assert.deepEqual(checkRequest(request('/tasks/'+ID+'/events','POST',{event_type,note:'离线验收说明',version_no:4})).errors,[]);
    assert.ok(validate({event_type,version_no:-1},contract.schemas.TaskEventCreate).length);
  }
  assert.deepEqual(checkRequest(request('/tasks?status=pending_review')).errors,[]);
  assert.ok(checkRequest(request('/tasks?status=unrecognized-state')).errors.length);
});
