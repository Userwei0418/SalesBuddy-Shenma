import test from 'node:test';
import assert from 'node:assert/strict';
import {LatestRead, appendPage, clampWidth, configStorageBytes, fieldLabel, editFilter, saveFilter, normalizeFilter, rowCanEditInline, configureColumns, decimalShift, editValue, formatValue, moveColumn, presentationError, querySignature, rowCanEdit, saveValue, saveEditValue, tableRows} from '../department-ui/pc/opportunity-workspace.js';
import {opportunityWorkspaceApi} from '../department-ui/pc/opportunity-workspace-api.js';

const fields = [{key: 'name', label: '商机', type: 'text', width: 240, filter_ops: ['contains'], sortable: true}, {key: 'amount', label: 'ACV', type: 'money', editable: true, width: 140, sortable: true}, {key: 'owner', label: '销售', type: 'person', groupable: true}];
const config = () => ({schema_version: 2, columns: [{key: 'name', width: 240}, {key: 'amount', width: 140}], scope: {kind: 'self'}, period: {kind: 'current_quarter'}, filters: [], sorts: [], group_by: null, density: 'compact', wrap: false});

test('editable-field contract rejects blank required names without changing optional clears or filter semantics', () => {
  const name = {key:'name',label:'商机名称',type:'text',nullable:false,max_length:200};
  for (const value of ['', '   ', null, undefined]) assert.throws(()=>saveEditValue(value,name),/请填写商机名称/);
  assert.throws(()=>saveEditValue('甲'.repeat(201),name),/商机名称最多填写 200 字/);
  assert.equal(saveEditValue('𠮷'.repeat(200),name),'𠮷'.repeat(200));
  assert.equal(saveEditValue('',{type:'text',nullable:true}),null);
  assert.equal(saveEditValue(false,{type:'boolean',nullable:true}),false);
  assert.equal(saveEditValue('0',{type:'money',nullable:true}),'0');
  assert.deepEqual(saveEditValue([],{type:'multi_enum',nullable:true}),[]);
  assert.deepEqual(saveFilter({field:'name',operator:'is_empty',value:''},[name]),{field:'name',operator:'is_empty'});
});

test('a saved board and an editor use the same membership config; presentation changes do not refetch or mutate it', () => {
  const saved = config(), original = structuredClone(saved);
  const edited = {...saved, columns: moveColumn(saved.columns, 'amount', 'name'), wrap: true, density: 'comfortable'};
  edited.columns = edited.columns.map(column => ({...column, width: 320}));
  assert.equal(querySignature(edited), querySignature(saved));
  assert.deepEqual(saved, original);
  assert.notEqual(querySignature({...saved, filters: [{field: 'name', operator: 'contains', value: '目标'}]}), querySignature(saved));
  assert.notEqual(querySignature({...saved, period: {kind: 'fixed', year: 2026, quarter: 4}}), querySignature(saved));
  assert.notEqual(querySignature({...saved, scope: {kind: 'authorized'}}), querySignature(saved));
});

test('selected fields retain identity and configured width without importing an unknown field', () => {
  const selected = configureColumns(config(), ['amount', 'owner', 'not-allowed', 'amount'], fields);
  assert.deepEqual(selected.columns.map(column => column.key), ['name', 'amount', 'owner']);
  assert.equal(selected.columns[1].width, 140);
  assert.equal(presentationError(selected, fields), '');
  assert.match(presentationError({...selected, columns: [...selected.columns, {key: 'removed'}]}, fields), /已不可用/);
  assert.match(presentationError({...selected, sorts: [{field: 'owner', direction: 'asc'}]}, fields), /排序/);
  assert.match(presentationError({...selected, group_by: 'amount'}, fields), /分组/);
});

test('multibyte filter content respects the complete config limit before query or save', () => {
  const small = {...config(), filters:[{field:'name',operator:'contains',value:'中'.repeat(5000)}]};
  assert.equal(presentationError(small,fields),'');
  const large = {...small, filters:Array.from({length:3},()=>small.filters[0])};
  assert.ok(configStorageBytes(large)>32768);assert.match(presentationError(large,fields),/内容过多/);
  assert.match(presentationError({...small,q:'非法\0字符'},fields),/无效字符/);
  assert.equal(configStorageBytes({...config(),q:'中'}),configStorageBytes({...config(),q:'a'})+2);
  assert.equal(configStorageBytes({...config(),group_by:null}),configStorageBytes({...config(),group_by:undefined}));
});

test('full-result order is preserved through pages and contiguous server groups', () => {
  const rows = [{id: 'open-q1', group_key: 'q1', group_label: 'Q1'}, {id: 'open-q4', group_key: 'q4', group_label: 'Q4'}];
  const appended = appendPage(rows, {items: [{id: 'open-q4-next', group_key: 'q4', group_label: 'Q4'}, {id: 'closed-q1', group_key: 'q1', group_label: 'Q1'}], total: 4, has_more: false}, true);
  assert.deepEqual(tableRows(appended, 'quarter').filter(row => !row.__group).map(row => row.id), ['open-q1', 'open-q4', 'open-q4-next', 'closed-q1']);
  assert.deepEqual(tableRows(appended, 'quarter').filter(row => row.__group).map(row => row.label), ['Q1', 'Q4', 'Q1']);
  assert.throws(() => appendPage(rows, {items: [rows[0]], total: 3}, true), /发生变化/);
  assert.throws(() => appendPage([], {items: [{id: 'one'}], total: 2, has_more: true, next_offset: 0}, false), /分页/);
  assert.throws(() => appendPage([], {items: [], total: null}, false), /响应不完整/);
});

test('financial input preserves cents, zero and missing data in both directions', () => {
  for (const [yuan, wan] of [['0', '0'], ['0.01', '0.000001'], ['1234567.89', '123.456789'], ['999999999999.99', '99999999.999999']]) {
    assert.equal(decimalShift(yuan, -4), wan);
    assert.equal(decimalShift(wan, 4), yuan);
  }
  assert.equal(editValue(null, {type: 'money'}), '');
  assert.equal(saveValue('', {type: 'money'}), null);
  assert.equal(saveValue('0', {type: 'money'}), '0');
  assert.equal(formatValue(0, {type: 'money'}), '0');
  assert.equal(formatValue(null, {type: 'money'}), '—');
  assert.throws(() => decimalShift('1e9', 4), /有效金额/);
  assert.throws(() => saveValue('no', {type: 'number'}), /有效整数/);
});

test('permission to configure views never supplies permission to edit a peer opportunity', () => {
  const field = {key: 'amount', editable: true};
  assert.equal(rowCanEdit({can_edit: true, capabilities: {editable_fields: []}}, field), false);
  assert.equal(rowCanEdit({capabilities: {editable_fields: ['amount']}}, field), true);
  assert.equal(rowCanEdit({capabilities: {editable_fields: ['actual']}}, {key: 'actual', editable: false}), false);
  assert.equal(saveValue(false, {type: 'boolean'}), false);
});

test('late requests cannot replace another filter or account, including late failures', async () => {
  const guard = new LatestRead();
  let owner = 'company-a:sales-a', resolveOld, rejectOld;
  const old = guard.run(owner, () => owner, () => new Promise(resolve => {resolveOld = resolve;}));
  const latest = await guard.run(owner, () => owner, async () => ({items: ['new']}));
  resolveOld({items: ['old']});
  assert.equal((await old).current, false); assert.equal(latest.current, true);
  const switched = guard.run(owner, () => owner, () => new Promise((_, reject) => {rejectOld = reject;}));
  owner = 'company-b:sales-b'; rejectOld(Error('old company failed'));
  assert.deepEqual(await switched, {current: false});
  const abandoned = guard.run(owner, () => owner, () => new Promise(resolve => {resolveOld = resolve;}));
  guard.invalidate(); resolveOld({}); assert.equal((await abandoned).current, false);
});

test('workspace saves and reads through the authenticated API, never local storage', async () => {
  const previous = globalThis.SalesRuntime, calls = [];
  let owner = 'workspace:person:permission-version';
  globalThis.SalesRuntime = {app: {globalData: {session: {userId: 'person'}}}, require(name) {
    return name === 'utils/access' ? {identity: () => owner} : {request: async request => {calls.push(request); return {id: 'view', config: request.data?.config};}};
  }};
  try {
    await opportunityWorkspaceApi.create({name: '团队周会', visibility: 'private', config: config()});
    await opportunityWorkspaceApi.query({config: config(), offset: 0, limit: 40});
    await opportunityWorkspaceApi.setDefault('view', 2);
    await opportunityWorkspaceApi.edit('opportunity', {version_no: 3, reason: '补充说明', values: {summary: '进展已确认'}});
    assert.deepEqual(calls.map(call => [call.method, call.path]), [['POST', '/opportunity-views'], ['POST', '/opportunity-views/query'], ['PUT', '/opportunity-views/preference'], ['PATCH', '/opportunities/opportunity/fields']]);
    assert.equal(calls[3].data.version_no, 3);
    assert.deepEqual(calls[2].data, {default_view_id: 'view', version_no: 2});
    globalThis.SalesRuntime.require = name => name === 'utils/access' ? {identity: () => owner} : {request: async () => {owner = 'another-account'; return {items: ['private']};}};
    await assert.rejects(opportunityWorkspaceApi.views(), /身份或权限/);
  } finally {globalThis.SalesRuntime = previous;}
});

 test('empty filters omit values and interval/column bounds follow schema2', () => {
  assert.deepEqual(normalizeFilter({field:'summary',operator:'is_empty',value:null}), {field:'summary',operator:'is_empty'});
  assert.throws(() => normalizeFilter({field:'amount',operator:'between',value:['0',null]}), /补全/);
  assert.deepEqual(normalizeFilter({field:'amount',operator:'between',value:['0','100']}), {field:'amount',operator:'between',value:['0','100']});
  assert.equal(clampWidth(0), 160); assert.equal(clampWidth(40),80); assert.equal(clampWidth(800),600);
  const row={capabilities:{editable_fields:['amount','summary']}};
  assert.equal(rowCanEditInline(row,{key:'amount',editable:true,edit_mode:'panel'}), false);
  assert.equal(rowCanEditInline(row,{key:'summary',editable:true,edit_mode:'inline'}),true);
 });

test('money filters roundtrip display units once and only validate at apply', () => {
 const money=[{key:'amount',type:'money'}];
 const original={field:'amount',operator:'between',value:['0.01','123456.78']};
 const edit=editFilter(original,money);
 assert.deepEqual(edit.value,['0.000001','12.345678']);
 assert.deepEqual(saveFilter(edit,money),original);
 assert.deepEqual(saveFilter({...edit,operator:'is_empty',value:null},money),{field:'amount',operator:'is_empty'});
 assert.throws(()=>saveFilter({...edit,value:['1.','bad']},money),/有效金额/);
});

test('datetime filters use explicit Shanghai offset and count filters require integers', () => {
 const f={key:'latest_followup_at',type:'datetime'};
 assert.equal(editValue('2026-12-31T16:30:00Z',f),'2027-01-01T00:30:00');
 assert.equal(saveValue('2027-01-01T00:30',f),'2027-01-01T00:30:00+08:00');
 assert.throws(()=>saveValue('1.5',{type:'number'}),/整数/);
});

test('money display never rounds a cent into zero or changes the value', () => {
 assert.equal(formatValue('0.01',{type:'money'}),'0.000001');
 assert.equal(formatValue('0.49',{type:'money'}),'0.000049');
 assert.equal(formatValue('0.50',{type:'money'}),'0.00005');
 assert.equal(formatValue('1234567890123456.78',{type:'money'}),'123,456,789,012.345678');
});


test('catalogue labels and full server group totals remain intact', () => {
 assert.equal(fieldLabel({label:'ACV（元）',type:'money'}),'ACV（万元）');
 assert.equal(fieldLabel({label:'ACV（万元）',type:'money'}),'ACV（万元）');
 const [group]=tableRows([{id:'one',group_key:'qualified',group_label:'qualified'}],'stage',[{key:'stage',options:[{value:'qualified',label:'商机确认'}]}],[{key:'qualified',count:31,amount:null,amount_unknown_count:1,known_amount_subtotal:'0.01'}]);
 assert.equal(group.label,'商机确认');assert.equal(group.summary.count,31);assert.equal(group.summary.known_amount_subtotal,'0.01');
});

test('server directory descriptors preserve query kind and never permit arbitrary URLs', async () => {
 const previous=globalThis.SalesRuntime,calls=[];
 globalThis.SalesRuntime={app:{globalData:{session:{userId:'person'}}},require(name){return name==='utils/access'?{identity:()=> 'owner'}:{request:async request=>{calls.push(request);return {items:[]}}};}};
 try{
  await opportunityWorkspaceApi.directory({url:'/api/v1/directory/opportunity-members?kind=fde',kind:'person'},{q:'王',offset:50});
  const url=new URL(calls[0].path,'https://test.invalid');assert.equal(url.searchParams.get('kind'),'fde');assert.equal(url.searchParams.get('q'),'王');assert.equal(url.searchParams.get('offset'),'50');
  assert.throws(()=>opportunityWorkspaceApi.directory({url:'https://other.invalid/users'}),/目录/);
 }finally{globalThis.SalesRuntime=previous;}
});
