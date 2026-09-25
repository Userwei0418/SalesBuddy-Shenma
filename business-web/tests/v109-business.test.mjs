import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const webRoot = fileURLToPath(new URL('../', import.meta.url));
const miniRoot = path.join(webRoot, 'source/miniprogram');
const previewScript = fs.readFileSync(path.join(webRoot, 'preview-api.js'), 'utf8');

function workspace(storage = new Map()) {
  const window = {SALES_MODE: 'preview', setTimeout, clearTimeout, localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)}};
  const wxStorage = new Map();
  const context = vm.createContext({window, URL, URLSearchParams, setTimeout, clearTimeout, console});
  vm.runInContext(previewScript, context, {filename: 'preview-api.js'});
  context.wx = {request: options => window.SalesPreview.request(options), uploadFile: options => window.SalesPreview.uploadFile(options),
    getStorageSync: key => wxStorage.get(key), setStorageSync: (key, value) => wxStorage.set(key, value), removeStorageSync: key => wxStorage.delete(key)};
  const cache = new Map();
  function load(filename) {
    const absolute = path.resolve(miniRoot, filename.endsWith('.js') ? filename : filename + '.js');
    if (cache.has(absolute)) return cache.get(absolute).exports;
    const module = {exports: {}}; cache.set(absolute, module);
    const wrapper = vm.runInContext('(function(require,module,exports){' + fs.readFileSync(absolute, 'utf8') + '\n})', context, {filename: absolute});
    wrapper(specifier => load(path.relative(miniRoot, path.resolve(path.dirname(absolute), specifier))), module, module.exports);
    return module.exports;
  }
  const api = load('utils/apiClient.js');
  const direct = (endpoint, options = {}) => new Promise((resolve, reject) => window.SalesPreview.request({url: 'https://example.invalid/api/v1' + endpoint, header: {Authorization: 'Bearer preview-access-sales'}, ...options, success: resolve, fail: reject}));
  return {window, api, direct, load, storage, context};
}


test('1.0.9 permissions use explicit grants, including mixed sales/FDE presentation and feature-specific scope',()=>{
 const w=workspace(),access=w.load('utils/access.js');
 const s={role:'sales',permissions:{'access.mini_program':true,'dashboard.read':true,'profile.fde_read':true,'battle_map.read':true,'opportunity.update':false},capabilities:{'opportunity.edit':true},permissionGrants:[{permission_code:'dashboard.read',effect:'allow',scope_code:'teams'}]};
 assert.equal(access.can(s,'opportunity.update'),false);
 assert.equal(access.canViewTeam(s,'dashboard.read'),true);assert.equal(access.canViewTeam(s,'battle_map.read'),false);
 assert.equal(access.presentationOptions(s,'bi').length,2);
 assert.equal(access.pageAllowed(s,'opportunity-create',{opportunityId:'one'}),false);
 assert.equal(access.pageAllowed({...s,permissions:{...s.permissions,'access.mini_program':false}},'bi'),false);
});
test('1.0.9 batch tasks create one per person atomically; invalid recipients leave no partial tasks',async()=>{
 const w=workspace();await w.api.loginWithAccount('PREVIEW_MANAGER','example');
 const base={description:'多人待办合成回归',associationKind:'daily',dueAt:Date.now()+86400000,priority:'高'};
 const before=await w.api.listTasks();
 const result=await w.api.createTasks([{...base,assigneeAccount:'PREVIEW_SALES'},{...base,assigneeAccount:'PREVIEW_FDE'}]);
 assert.equal(result.items.length,2);assert.notEqual(result.items[0].id,result.items[1].id);
 assert.notEqual(result.items[0].assignees[0].user_id,result.items[1].assignees[0].user_id);
 const count=(await w.api.listTasks()).items.length;
 await assert.rejects(w.api.createTasks([{...base,assigneeAccount:'PREVIEW_SALES'},{...base,assigneeAccount:'UNKNOWN'}]),/有效/);
 assert.equal((await w.api.listTasks()).items.length,count);
});
test('1.0.9 claim filter options compose industry and status and reset to all',async()=>{
 const w=workspace();await w.api.loginWithAccount('PREVIEW_SALES','example');
 const options=await w.api.listCustomerClaimOptions();assert.equal(options.industries[0].value,'');assert.equal(options.claim_statuses[0].value,'');
 const all=await w.api.listCustomerClaimPool({pageSize:50}), available=await w.api.listCustomerClaimPool({pageSize:50,industry:'企业软件',claimStatus:'unclaimed'});
 assert.ok(all.total>available.total);assert.ok(available.items.length);assert.ok(available.items.every(c=>c.industry_code==='企业软件'&&c.claim_status==='unclaimed'));
 assert.equal((await w.api.listCustomerClaimPool({pageSize:50})).total,all.total);
});
test('1.0.9 active map separates pending scores from valid zero and portfolio includes inactive customers',async()=>{
 const w=workspace();await w.api.loginWithAccount('PREVIEW_MANAGER','example');
 const map=await w.api.getCustomerMap(),assets=await w.api.getCustomerAssets({period:'all'});
 assert.match(map.activity_since,/^\d{4}-\d{2}-\d{2}$/);assert.ok(assets.summary.portfolio_customer_count>map.items.length);
 const {normalizeCustomerSummary}=w.load('utils/customerDetail.js'),{hasMapScores}=w.load('utils/customerMap.js');
 const pending=map.items.find(c=>c.potential_score===null),zero=map.items.find(c=>c.potential_score===0);
 assert.ok(pending&&zero);assert.equal(hasMapScores(normalizeCustomerSummary(pending)),false);assert.equal(hasMapScores(normalizeCustomerSummary(zero)),true);
 const year=await w.api.getCustomerAssets({period:'year'});assert.equal(year.summary.portfolio_customer_count,assets.summary.portfolio_customer_count);assert.equal(year.summary.acv_amount,assets.summary.acv_amount);
});
test('1.0.9 team follow-up ranking includes zero members and excludes empty teams from ranks',async()=>{
 const w=workspace();await w.api.loginWithAccount('PREVIEW_MANAGER','example');
 const year=new Date().getFullYear(),get=async quarter=>(await w.direct('/dashboard/rankings?personal=false&year='+year+'&quarters='+quarter,{header:{Authorization:'Bearer preview-access-manager'}})).data;
 const ranks=await get(3),other=await get(4),f=ranks.followup;
 assert.equal(f.calculation,'team_followup_per_capita_v1');
 const full=f.rows.find(r=>r.member_count>0),empty=f.rows.find(r=>r.member_count===0);
 assert.ok(full.members.some(m=>m.followup_count===0));assert.equal(full.value,full.record_count/full.member_count);assert.equal(empty.value,null);assert.equal(empty.rank,null);
 assert.deepEqual(f.rows,other.followup.rows,'follow-up seven-day window is independent of forecast quarters');
});
test('1.0.9 history preserves raw amount/tax and cross-subject links do not inject the enclosing customer',()=>{
 const w=workspace(),{normalizeCustomerDetail}=w.load('utils/customerDetail.js'),{visitDetailUrl}=w.load('utils/visitNavigation.js');
 const raw={id:'customer-a',name:'合成客户',opportunities:[{id:'op-a',original_owner_name:'原负责人',ownership_resolution:'provisional',expected_close_year:2026,expected_close_quarter:3,historical_period_actuals:[{year:2025,quarter:4,kind:'recognized',raw_amount:'12.3400',source_unit:'wan_cny',tax_basis:'exclusive',source_field:'原始季度字段'}],quarterly_forecasts:[{year:2026,quarter:3,collection_amount:0,collection_confidence:'low'}]}],visits:[{id:'visit-a',partner_id:'partner-a',original_recorder_name:'原记录人',linked_opportunities:[{id:'op-a',name:'甲商机'},{id:'op-b',name:'乙商机'}]}]};
 const d=normalizeCustomerDetail(raw),o=d.opportunities[0];assert.equal(o.historicalPeriodRecords[0].amountText,'12.3400万元');assert.equal(o.historicalPeriodRecords[0].taxBasisText,'不含税');
 assert.equal(o.expectedDate,'2026 Q3');assert.equal(o.originalOwnerName,'原负责人');assert.match(o.quarterDetails[0].collectionConfidenceText,/低/);
 assert.equal(d.visits[0].linkedOpportunities.length,2);assert.equal(d.visits[0].owner,'原记录人');
 assert.equal(visitDetailUrl(raw.visits[0]),'/pages/visit-detail/index?visit_id=visit-a');
});
test('1.0.9 all-stock opportunity total is independent of summary quarter; active excludes lost and requires follow-up',async()=>{
 const w=workspace();await w.api.loginWithAccount('PREVIEW_MANAGER','example');
 const request=q=>w.direct('/opportunities/overview?year=2026'+q,{header:{Authorization:'Bearer preview-access-manager'}});
 const all=(await request('')).data,q1=(await request('&quarters=1')).data,q4=(await request('&quarters=4')).data;
 assert.equal(q1.metrics.total,all.metrics.total);assert.equal(q4.metrics.total,all.metrics.total);assert.ok(all.metrics.active<=all.metrics.total);
});
