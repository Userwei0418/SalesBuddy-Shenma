const test=require('node:test');
const assert=require('node:assert/strict');
const {historySide,intentHistoryItem}=require('../miniprogram/utils/opportunityIntentHistory');
const A='3724fd08-4d7d-474c-8a25-186f715f71aa',B='6ca9510e-895c-417f-a069-cfb4d389df59';
const snapshot={version:1,basis:'directory_at_change',before:[{id:A,name:'原名称A',status:'recorded'}],after:[{id:B,name:'原名称B',status:'recorded'},{id:A,name:'原名称A',status:'recorded'}]};

test('intent history keeps saved old names and matches IDs rather than snapshot order',()=>{
 const field={field:'intent_product_line_ids',before:[A],after:[A,B],display_snapshot:snapshot};
 const result=intentHistoryItem({id:'change',version_no:2,actor:{name:'销售甲'},created_at:'2026-10-11T17:57:08Z',fields:[field]});
 assert.equal(result.actor,'销售甲');assert.equal(result.versionLabel,'商机版本 2');
 assert.deepEqual(result.changes[0].before.rows.map(row=>row.name),['原名称A']);
 assert.deepEqual(result.changes[0].after.rows.map(row=>row.name),['原名称A','原名称B']);
 assert.equal(result.unavailable,false);assert.match(result.at,/2026-10/);
});
test('absent, wrong-side, unsupported and unavailable snapshots explicitly preserve unknown IDs',()=>{
 for(const value of [null,{...snapshot,version:2},{...snapshot,basis:'current_directory'},{...snapshot,after:[{id:B,name:'现在名称',status:'recorded'}]},{...snapshot,after:[{id:A,name:'现在名称',status:'unavailable'}]}]){
  assert.deepEqual(historySide([A],value,'after').rows,[{id:A,name:'名称未记录',missingName:true}]);
 }
});
test('duplicate or nameless snapshot entries cannot supply a misleading historic name',()=>{
 for(const rows of [[{id:A,name:'',status:'recorded'}],[{id:A,name:{name:'wrong'},status:'recorded'}],[{id:A,name:'甲',status:'recorded'},{id:A,name:'乙',status:'recorded'}]]){
  assert.equal(historySide([A],{...snapshot,after:rows},'after').rows[0].missingName,true);
 }
});
test('recorded empty selection differs from missing or malformed historic content',()=>{
 assert.equal(historySide([],snapshot,'before').emptyLabel,'未选择产品线');
 for(const value of [null,undefined,'',{},[null],[{id:A}]])assert.equal(historySide(value,snapshot,'before').emptyLabel,'历史内容未完整记录');
});
test('only intended field is presented; raw unknown business payloads are not stringified',()=>{
 const result=intentHistoryItem({id:'x',actor:{},reason:{private:'data'},fields:[{field:'operational_actual',after:{amount:1}},{field:'note',after:'other'}]});
 assert.equal(result.unavailable,true);assert.deepEqual(result.changes,[]);assert.equal(result.reason,'');assert.equal(result.actor,'操作人未记录');assert.equal(result.versionLabel,'版本未记录');
});
test('formatter does not mutate stored history data',()=>{
 const input={id:'x',fields:[{field:'intent_product_line_ids',before:[A],after:[A,B],display_snapshot:snapshot}]};
 const saved=JSON.stringify(input);intentHistoryItem(input);assert.equal(JSON.stringify(input),saved);
});
