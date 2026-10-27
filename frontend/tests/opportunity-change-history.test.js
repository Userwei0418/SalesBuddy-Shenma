const test=require('node:test');
const assert=require('node:assert/strict');
const {historyValue,changeHistoryItem}=require('../miniprogram/utils/opportunityChangeHistory');
// Backend change-history v1 synthetic fixture; actor.name is the unannotated name.
const fixture=require('./fixtures/opportunity-change-history.v1.json');
const event=()=>structuredClone(fixture.changes.items[0]);

test('frozen backend fixture displays all recorded fields once with units and saved names',()=>{
 const raw=event(),saved=JSON.stringify(raw),view=changeHistoryItem(raw);
 assert.equal(view.id,raw.id);assert.equal(view.at,'2026-10-20 15:30');assert.equal(view.versionLabel,'记录版本 3');
 assert.equal(view.changes.length,raw.fields.length);assert.equal(new Set(view.changes.map(x=>x.key)).size,raw.fields.length);
 const byLabel=Object.fromEntries(view.changes.map(x=>[x.label,x]));
 assert.equal(byLabel['ACV（万元）'].before.rows[0].name,'10');
 assert.equal(byLabel['ACV（万元）'].after.rows[0].name,'20');
 assert.equal(byLabel['2026 Q4 预测回款（万元）'].after.rows[0].name,'12');
 assert.equal(byLabel['意向产品线'].after.rows[0].name,'保存时的产品线名称');
 assert.equal(byLabel['交易物料'].after.rows[0].name,'保存时的物料名称');
 assert.match(byLabel['交易物料'].after.rows[0].detail,/数量：2；成交单价（元）：5000/);
 assert.equal(byLabel['商机阶段'].before.rows[0].id,null);
 assert.equal(byLabel['商机阶段'].before.emptyLabel,'');
 assert.equal(view.partial,true);assert.equal(view.unavailable,false);assert.equal(view.unavailableFieldCount,3);
 assert.equal(JSON.stringify(raw),saved);
});

test('missing person names retain true IDs and role detail without treating roles as names',()=>{
 const view=changeHistoryItem(event()),people=view.changes.find(x=>x.label==='项目参与人员');
 assert.equal(people.before.status,'empty');assert.equal(people.before.emptyLabel,'无');
 assert.equal(people.after.rows[0].name,'名称未记录');
 assert.equal(people.after.rows[0].detail,'销售参与者');
 assert.equal(people.after.rows[0].id,fixture.changes.items[0].fields[3].after.rows[0].id);
 assert.equal(people.after.rows[0].missingName,true);
});

test('restricted, missing, empty and truncated values remain distinct',()=>{
 const restricted=changeHistoryItem(event()).changes.find(x=>x.label==='运营期间实际').after;
 assert.equal(restricted.status,'restricted');assert.match(restricted.emptyLabel,/无权查看/);
 assert.doesNotMatch(restricted.emptyLabel,/未记录/);
 const missing=historyValue({kind:'unavailable',status:'unavailable',rows:[],empty_label:'历史内容未记录',truncated:false});
 assert.equal(missing.emptyLabel,'历史内容未记录');assert.equal(missing.status,'unavailable');
 const raw=event().fields[0].after;raw.truncated=true;
 const truncated=historyValue(raw);assert.equal(truncated.rows[0].name,'商机确认－30%');assert.equal(truncated.truncated,true);
 assert.equal(truncated.emptyLabel,'');
});

test('current actor names are labelled and unavailable names retain the recorded actor ID',()=>{
 const raw=event();raw.actor.name='销售甲';raw.actor.name_status='current';
 assert.equal(changeHistoryItem(raw).actor,'销售甲（当前姓名）');
 raw.actor.name_status='recorded';assert.equal(changeHistoryItem(raw).actor,'销售甲');
 raw.actor.name_status='unavailable';assert.equal(changeHistoryItem(raw).actor,`操作人姓名未记录（用户 ID：${raw.actor.id}）`);
 raw.actor.id=null;assert.equal(changeHistoryItem(raw).actor,'操作人姓名未记录');
});

test('zero, multiline text and markup-like content remain literal display text',()=>{
 const raw=event(),side=raw.fields[0].after;
 side.rows[0].name='0\n<script>not executed</script>';side.rows[0].detail='第一行\n第二行';
 const view=changeHistoryItem(raw);
 assert.equal(view.changes[0].after.rows[0].name,'0\n<script>not executed</script>');
 assert.equal(view.changes[0].after.rows[0].detail,'第一行\n第二行');
});

test('invalid display values reject explicitly instead of leaking arbitrary change JSON',()=>{
 for(const mutate of [
  x=>x.fields[0].after.rows[0].name={secret:'must not stringify'},
  x=>x.fields[0].after.status='unknown',
  x=>x.fields[0].after={before:'raw',after:{secret:'payload'}},
  x=>x.fields[0].after.rows[0].missing_name='false',
  x=>x.fields[0].after.status='restricted',
  x=>x.actor.name_status='guessed',
  x=>x.created_at='not a date',
  x=>x.version_no=0,
  x=>x.fields=null,
 ]){
  const raw=event();mutate(raw);assert.throws(()=>changeHistoryItem(raw),/响应不完整/);
 }
});
