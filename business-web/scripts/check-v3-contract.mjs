// Offline request-contract audit. The VM has no fetch, network, filesystem or real credentials.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const miniRoot = path.join(root, 'source/miniprogram');
export const contract = JSON.parse(fs.readFileSync(process.env.V3_CONTRACT_FILE || path.join(root, 'tests/fixtures/v3-contract.json'), 'utf8'));
const ID = '00000001-0000-4000-8000-000000000001';
const OP = '00000002-0000-4000-8000-000000000001';
const RUN = '00000003-0000-4000-8000-000000000001';
const businessOptionsFixture = contract.offline_business_options_fixture;
const ref = schema => schema?.$ref ? contract.schemas[schema.$ref.split('/').at(-1)] : schema;
export function validate(value, schema = {}, location = 'value', coerce = false) {
  schema = ref(schema) || {}; const errors = [];
  if (schema.anyOf) return schema.anyOf.some(s => validate(value, s, location, coerce).length === 0) ? [] : [`${location}: does not match any allowed type/value`];
  if (schema.allOf) return schema.allOf.flatMap(s => validate(value, s, location, coerce));
  let v = value;
  if (coerce && typeof v === 'string') {
    if (['integer', 'number'].includes(schema.type) && /^-?\d+(?:\.\d+)?$/.test(v)) v = Number(v);
    if (schema.type === 'boolean' && ['true', 'false'].includes(v)) v = v === 'true';
  }
  const typeOk = !schema.type || ({null: v === null, string: typeof v === 'string', number: typeof v === 'number' && Number.isFinite(v), integer: Number.isInteger(v),
    boolean: typeof v === 'boolean', object: v !== null && typeof v === 'object' && !Array.isArray(v), array: Array.isArray(v)})[schema.type];
  if (!typeOk) return [`${location}: expected ${schema.type}`];
  if (schema.enum && !schema.enum.includes(v)) errors.push(`${location}: invalid enum ${JSON.stringify(v)}`);
  if (Object.hasOwn(schema, 'const') && v !== schema.const) errors.push(`${location}: expected const ${JSON.stringify(schema.const)}`);
  if (typeof v === 'string') {
    if (schema.minLength != null && v.length < schema.minLength || schema.maxLength != null && v.length > schema.maxLength) errors.push(`${location}: invalid length`);
    if (schema.pattern && !new RegExp(schema.pattern).test(v)) errors.push(`${location}: invalid pattern`);
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)) errors.push(`${location}: invalid UUID`);
    if (schema.format === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(v)) errors.push(`${location}: invalid date`);
    if (schema.format === 'date-time' && (!/T/.test(v) || !Number.isFinite(Date.parse(v)))) errors.push(`${location}: invalid date-time`);
  }
  if (typeof v === 'number') for (const [key, compare] of [['minimum', n => v < n], ['maximum', n => v > n], ['exclusiveMinimum', n => v <= n], ['exclusiveMaximum', n => v >= n]]) if (schema[key] != null && compare(schema[key])) errors.push(`${location}: violates ${key}`);
  if (Array.isArray(v)) {
    if (schema.minItems != null && v.length < schema.minItems || schema.maxItems != null && v.length > schema.maxItems) errors.push(`${location}: invalid item count`);
    v.forEach((item, i) => errors.push(...validate(item, schema.items, `${location}[${i}]`, coerce)));
  } else if (v && typeof v === 'object') {
    for (const key of schema.required || []) if (!Object.hasOwn(v, key)) errors.push(`${location}.${key}: required`);
    for (const [key, item] of Object.entries(v)) if (schema.properties?.[key]) errors.push(...validate(item, schema.properties[key], `${location}.${key}`));
    else if (schema.additionalProperties === false) errors.push(`${location}.${key}: undeclared field`);
  }
  return errors;
}
export function checkRequest(request) {
  const url = new URL(request.url), method = (request.method || 'GET').toUpperCase();
  const operation = [...contract.operations].sort((a,b) => (a.path.match(/\{/g)||[]).length - (b.path.match(/\{/g)||[]).length)
    .find(o => o.method === method && new RegExp('^' + o.path.replace(/\{[^}]+\}/g, '[^/]+') + '$').test(url.pathname));
  if (!operation) return {operation: null, errors: [`Undeclared operation: ${method} ${url.pathname}`], warnings: []};
  const errors = [], warnings = [], query = new Map((operation.parameters || []).filter(p => p.in === 'query').map(p => [p.name, p]));
  for (const name of new Set(url.searchParams.keys())) {
    const parameter = query.get(name);
    if (!parameter) {warnings.push(`Undeclared query parameter: ${name}`); continue;}
    const schema = ref(parameter.schema), value = schema.type === 'array' ? url.searchParams.getAll(name) : url.searchParams.get(name);
    errors.push(...validate(value, schema, 'query.' + name, true));
  }
  for (const p of query.values()) if (p.required && !url.searchParams.has(p.name)) errors.push(`query.${p.name}: required`);
  const templateParts = operation.path.split('/'), actualParts = url.pathname.split('/');
  for (const p of operation.parameters || []) {
    if (p.in === 'path') errors.push(...validate(decodeURIComponent(actualParts[templateParts.indexOf('{' + p.name + '}')]), p.schema, 'path.' + p.name));
    if (p.in === 'header') {
      const entry = Object.entries(request.header || {}).find(([name]) => name.toLowerCase() === p.name.toLowerCase());
      if (entry) errors.push(...validate(entry[1], p.schema, 'header.' + p.name));
      else if (p.required) errors.push(`header.${p.name}: required`);
    }
  }
  const body = operation.requestBody, schema = body?.content?.[request.multipart ? 'multipart/form-data' : 'application/json']?.schema;
  if (schema && request.data !== undefined) errors.push(...validate(request.data, schema, 'body'));
  else if (body?.required && request.data === undefined) errors.push('body: required');
  const mutationKey = operation.parameters?.some(p => p.in === 'header' && p.name.toLowerCase() === 'idempotency-key');
  if (mutationKey && !Object.keys(request.header || {}).some(name => name.toLowerCase() === 'idempotency-key')) warnings.push('OpenAPI supports Idempotency-Key; this sample omits the optional header');
  return {operation: `${method} ${operation.path}`, errors, warnings, mutationKey: !!mutationKey, responseStatuses: Object.keys(operation.responses).filter(code => code.startsWith('2'))};
}
export async function auditClient() {
  const captured = [], storage = new Map(), cache = new Map(); let scenario = '';
  const actor = {workspace_id: ID, user_id: OP, role: 'sales', account_code: 'OFFLINE_TEST'};
  const auth = {access_token: 'offline-token-never-sent', refresh_token: 'offline-refresh-never-sent', actor};
  storage.set('salesApiBaseUrl', 'https://offline.invalid/api/v1');
  const canned = {id: ID, run_id: RUN, status: 'succeeded', result: {}, items: [], total: 0, has_more: false, next_offset: null, next_cursor: null, ...auth};
  const wx = {getStorageSync: k => storage.get(k), setStorageSync: (k, v) => storage.set(k, v), removeStorageSync: k => storage.delete(k),
    request: options => {
      // Match JSON serialization: undefined object properties never reach the HTTP body.
      const observed = {scenario, ...options, data: options.data === undefined ? undefined : JSON.parse(JSON.stringify(options.data))}; captured.push(observed);
      const check = checkRequest(observed), statusCode = Number(check.responseStatuses?.[0] || 200);
      const responseUrl = new URL(options.url);
      const response = responseUrl.pathname.endsWith('/metadata/business-options') ? businessOptionsFixture
        : responseUrl.pathname.endsWith('/visits') && responseUrl.searchParams.get('sort') === 'created_desc' ? {...canned,sort:'created_desc'} : canned;
      queueMicrotask(() => options.success({statusCode, data: statusCode === 204 ? undefined : response}));
    },
    uploadFile: options => {captured.push({scenario, url: options.url, method: 'POST', header: options.header, multipart: true, data: {...options.formData, [options.name]: 'offline-file'}}); queueMicrotask(() => options.success({statusCode: 200, data: JSON.stringify({text: 'offline transcript'})}));}};
  const context = vm.createContext({wx, console, setTimeout, clearTimeout});
  function load(filename) {
    const absolute = path.resolve(miniRoot, filename.endsWith('.js') ? filename : filename + '.js');
    if (!absolute.startsWith(miniRoot + path.sep)) throw Error('Offline harness prevents loading outside mini-program source');
    if (cache.has(absolute)) return cache.get(absolute).exports;
    const module = {exports: {}}; cache.set(absolute, module);
    vm.runInContext('(function(require,module,exports){' + fs.readFileSync(absolute, 'utf8') + '\n})', context, {filename: absolute})(
      name => load(path.relative(miniRoot, path.resolve(path.dirname(absolute), name))), module, module.exports);
    return module.exports;
  }
  const api = load('utils/apiClient.js');
  const customer = {name: '离线契约样本', customer_type: '潜在客户', level_code: 'Tier-2', source: '市场活动', target_team: '离线团队', target_team_id: ID, contact_name: '离线联系人', contact_title: '离线职位', contact_role: '决策者'};
  // Form serialization uses the same server-owned metadata as the application.
  // This fixture is isolated to the offline audit; runtime never falls back to it.
  load('utils/businessOptions.js').install(businessOptionsFixture);
  const form = load('utils/opportunity.js'), opportunityForm = form.formFor(null);
  Object.assign(opportunityForm, {name: '离线商机', amount: '12.3456', expected_close_date: '2026-12-31', stageIndex: 2, quarters: [{year: 2026, quarter: 4, recognized: '0', collection: '2'}]});
  const opportunity = form.payload(opportunityForm, null);
  const cases = [
    ['loginWithAccount', ['OFFLINE_TEST', 'offline-only', 'sales']], ['getCurrentActor', []], ['getBusinessOptions', []], ['getAssistantHome', []],
    ['listCustomers', [{scope: 'company', q: '离线', unassigned: false}]], ['listCustomerClaimPool', [{q: '离线', offset: 50, pageSize: 50}]],
    ['createCustomer', [customer]], ['updateCustomer', [ID, {industry: '企业软件', contact_title: '离线维护职位'}]], ['assignCustomer', [ID, {assignee_account_code: 'OFFLINE_TEST', first_action: '离线首步行动'}]], ['claimCustomer', [ID]],
    ['getCustomer', [ID]], ['getCustomerReference', [ID]], ['getCustomerHeader', [ID]], ['getCustomerOverview', [ID]],
    ['listCustomerContacts', [ID, {page_size: 20, offset: 20}]], ['listCustomerOpportunities', [ID, {page_size: 20, offset: 20}]],
    ['getDirectoryMembers', []], ...['browse','dashboard','profile','fde','assignment'].map(purpose => ['getTeamDirectory', [purpose]]), ['getTaskAssignees', [{customer_id: ID, opportunity_id: OP}]], ['getTaskPositions', [{customer_id: ID, opportunity_id: OP}]],
    ['listPartners', [{q: '离线', offset: 50}]], ['listFdeMembers', [{q: '离线', offset: 50}]],
    ['createOpportunity', [ID, opportunity]], ['checkOpportunityName', [ID, '离线商机', OP]],
    ['listOpportunities', [{customerId: ID, scope: 'self', memberId: OP, stages: ['identified', 'won'], year: 2026, quarters: [1, 4], includeClosed: true, pageSize: 20, offset: 20}]],
    ['getOpportunityOverview', [{year: 2026, quarters: [1, 4]}]], ['getOpportunityDetail', [OP]], ['getOpportunityDetailHeader', [OP]], ['getOpportunityDetailOverview', [OP]],
    ['getCustomerOpportunityHeader', [ID, OP]], ['getCustomerOpportunityOverview', [ID, OP]], ['getOpportunityTimeline', [OP, {customer_id: ID, page_size: 20, offset: 20}]], ['updateFdeMembers', [OP, [ID], 3]],
    ['createTask', [{description: '离线指定同事任务', associationKind: 'customer', assigneeAccount: 'OFFLINE_TEST', dueAt: '2026-12-31T10:00:00+08:00', priority: '高', customerId: ID, opportunityId: OP}]],
    ['createTask', [{description: '离线日常协作任务', associationKind: 'daily', assigneeAccount: 'OFFLINE_TEST', dueAt: '2026-12-31T10:00:00+08:00'}]],
    ['getTask', [ID]], ['listTaskPage', [{tab: 'all', view: 'team', member_id: OP, page_size: 20, offset: 20, completed_year: 2026, completed_quarters: [1, 4], order: 'created_desc'}]],
    ['listTasks', ['pending_confirm', 20, {view: 'self'}]], ['listDetailTasks', [{customer_id: ID, opportunity_id: OP, page_size: 20, offset: 20}]], ['getTaskOverview', [[ID, OP]]],
    ['respondTask', [ID, 'accept', '', 3]], ['respondTask', [ID, 'reject', '离线拒绝原因', 3]], ['completeTask', [ID, '离线完成反馈', 3]],
    ['respondTask', [ID, 'approve_completion', '离线确认完成', 4]], ['respondTask', [ID, 'reject_completion', '离线补充验收要求', 4]],
    ['coordinateTask', [ID, {event_type: 'reassign', assignee_account_code: 'OFFLINE_TEST', note: '离线协调原因', version_no: 3}]],
    ['coordinateTask', [ID, {event_type: 'cancel', note: '离线取消原因', version_no: 3}]],
    ['getVisitFormSchema', []], ['createVisit', [ID, {follow_up_record: '离线拜访', next_action: '明天确认清单', _quality_review_run_id: RUN, _opportunity_mutation: opportunity}, [OP]]],
    ['getVisit', [ID]], ['listVisits', [{customer_id: ID, opportunity_id: OP, cursor: 'opaque-offline-cursor', page_size: 20}]],
    ['listVisits', [{customer_id: ID, sort:'created_desc', page_size:20}]],
    ['request', [{path: '/directory/colleagues'}]], ['request', [{path: `/visits/${ID}`, method: 'PATCH', data: {version_no: 3, contact_name: '离线补充联系人'}}]],
    ['runAgent', ['visit_entry', '离线文本', ID, OP]], ['runAgent', ['opportunity_draft', '离线文本', ID]], ['transcribeAudio', ['/offline/example.webm', 'visit_entry']],
    ['getCustomerMap', [{scope: 'self'}]], ['getCustomerAssets', [{customer_id: ID, opportunity_id: OP, period: 'all', page_size: 20, offset: 20}]],
    ['getCustomerAssetQuarters', [{customer_id: ID, opportunity_id: OP, as_of: '2026-09-14'}]],
    ['createCustomerActual', [{customer_id: ID, opportunity_id: OP, kind: 'recognized', amount: '123.45', occurred_on: '2026-09-14', source_ref: 'OFFLINE-TEST', request_id: RUN, confirmed: true}]], ['voidCustomerActual', [ID, '离线作废原因']],
    ['listNotifications', [false]], ['markNotificationRead', [ID]], ['queryBusinessAdvice', ['visit', ID]], ['getBusinessAdvice', [ID]],
    ['decideSuggestion', [ID, {decision: 'no_task', reason: '离线无需任务', version_no: 1}]],
    ['getFdeDashboard', [{scope: 'self', quarters: [1, 4]}]], ['getFdeActivity', [{scope: 'self', quarters: [1, 4]}]], ['listFdeVisitOpportunities', [{customer_id: ID, opportunity_id: OP, limit: 50, offset: 0}]],
    ['listTaskRecipients', [{q: '离线', page_size: 50, offset: 50}]],
    ['listTaskCustomers', [{q: '离线', pageSize: 20, offset: 20}]],
    ['listTaskOpportunities', [{customerId: ID, opportunityId: OP, pageSize: 20, offset: 20}]],
    ['getDashboard', [false, {member_id: OP, team_groups: ['team:' + ID, 'team:' + OP]}]],
    ['getDashboardOptions', []], ['getDashboardRankings', [{year: 2026, quarters: [1,4], personal: false, member_id: OP, team_groups: ['team:' + ID, 'team:' + OP]}]],
    ['getProfileScopeOptions', []], ['getFdeScopeOptions', []],
    ['getFdeProfile', [{days: 30, scope: 'self'}]], ['reviewFdeProfile', [{days:30,scope:'self'}]],
    ['getTargets', [{scope: 'self', period_type: 'quarter', anchor_date: '2026-07-01'}]],
    ['saveTarget', [{scope: 'self', period_type: 'quarter', anchor_date: '2026-07-01', kind: 'collection', amount: '100.01', reason: '离线契约验证'}]],
    ['saveTargetBatch', [{scope: 'self', period_type: 'quarter', anchor_date: '2026-07-01', items: [{kind: 'collection', amount: '100.01', version_no: 1}, {kind:'recognized',amount:'200.00',version_no:null}], reason:'离线契约验证'}]],
    ['listDemoScenes', [OP, {limit: 20, offset: 20}]], ['getDemoScene', [ID]],
    ['createDemoScenes', [OP, [{name:'离线场景',description:'离线描述'}]]],
    ['updateDemoScene', [ID, {name:'离线场景更新',description:'离线描述',version_no:1}]], ['deleteDemoScene', [ID, 2]],
    ['submitVisitStage', ['structure', {customer_id:ID,opportunity_id:OP,text:'离线拜访内容',is_first_visit:false}]],
    ['submitVisitStage', ['quality', {customer_id:ID,opportunity_id:OP,source_run_id:RUN,fields:{customer_name:'离线客户',customer_type:'潜在客户',opportunity_name:'离线商机',follow_up_record:'离线拜访内容',next_action:'离线行动',interaction_at:'2026-09-15',created_date:'2026-09-15',contact_name:'离线联系人',partner_name:'',contact_title:'经理',interaction_mode:'线下拜访',visit_location:'离线地点',visit_goal:'了解需求',customer_main_business:'软件',customer_needs:'演示',customer_budget:'待确认',contact_role:'决策者',is_first_visit:false},summary:'离线摘要',collaborator_ids:[],fde_participant_ids:[]}]],
    ['listOpportunities', [{year:2026,quarters:[1,4],scope:'team',memberIds:[ID,OP],teamId:ID}]],
    ['getOpportunityOverview', [{year:2026,quarters:[1,4],scope:'team',memberIds:[ID,OP],teamId:ID}]],
    ['getCustomerMap', [{scope:'team',member_ids:[ID,OP]}]],
    ['getFdeDashboard', [{scope:'team',quarters:[1,4],member_ids:[ID,OP]}]],
  ];
  for (const [name, args] of cases) {scenario = name; if (typeof api[name] !== 'function') throw Error('Missing client method: ' + name); await api[name](...args);}
  const results = captured.map(request => ({scenario: request.scenario, ...checkRequest(request)}));
  const opBody = captured.find(r => r.scenario === 'createOpportunity').data, actualBody = captured.find(r => r.scenario === 'createCustomerActual').data;
  return {baseline: contract.baseline, source_sha256: contract.source_sha256, client_sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(miniRoot, 'utils/apiClient.js'))).digest('hex'),
    offline: true, realRequests: 0, scenarioCount: cases.length, requestCount: captured.length, coveredOperationCount: new Set(results.map(r => r.operation)).size,
    totalContractOperations: contract.operations.length, errors: results.flatMap(r => r.errors.map(message => ({scenario: r.scenario, operation: r.operation, message}))),
    warnings: results.flatMap(r => r.warnings.map(message => ({scenario: r.scenario, operation: r.operation, message}))),
    semantics: {opportunityAmountYuan: opBody.amount, recognizedForecastYuan: opBody.quarterly_forecasts[0].recognized_amount, collectionForecastYuan: opBody.quarterly_forecasts[0].collection_amount,
      actualAmountYuan: actualBody.amount, actualRequestIdPresent: !!actualBody.request_id, opportunityVersionOmittedOnCreate: !Object.hasOwn(opBody, 'version_no'),
      taskEventVersion: captured.find(r => r.scenario === 'completeTask').data.version_no,
      taskCompletionEvents: captured.filter(r => r.scenario === 'completeTask' || r.scenario === 'respondTask' && ['approve_completion','reject_completion'].includes(r.data.event_type)).map(r => ({event_type:r.data.event_type,version_no:r.data.version_no})),
      metadataRequestCount: captured.filter(r => new URL(r.url).pathname.endsWith('/metadata/business-options')).length,
      teamDirectoryPurposes: captured.filter(r => r.scenario === 'getTeamDirectory').map(r => new URL(r.url).searchParams.get('purpose')), dueAtUTC: captured.find(r => r.scenario === 'createTask').data.due_at}, results};
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const report = await auditClient();
  const at = process.argv.indexOf('--output');
  if (at >= 0) {const output = path.resolve(process.argv[at + 1]); fs.mkdirSync(path.dirname(output), {recursive: true}); fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');}
  const {results, ...summary} = report;
  console.log(JSON.stringify(process.argv.includes('--verbose') ? report : summary, null, 2)); process.exitCode = report.errors.length ? 1 : 0;
}
