const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../miniprogram/utils/taskExecutionPresentation');
const row=(extra={})=>({requirements_version:2,completed:1,total:3,percent:33,basis:'checklist',...extra});
test('服务器33%原样展示，100%不产生完成状态或KR贡献',()=>{
 assert.equal(view.progress(row()).label,'33%');
 const p=view.progress(row({completed:3,percent:100}));assert.equal(p.label,'100%');assert.match(p.hint,/提交与验收/);
 assert.equal(Object.hasOwn(p,'status'),false);assert.equal(Object.hasOwn(p,'kr_progress'),false);
});
test('缺清单、待复核与真实0%分别展示',()=>{
 const missing=view.progress(row({completed:0,total:0,percent:null,basis:'missing_checklist'}));
 assert.equal(missing.percent,null);assert.equal(missing.label,'推进依据待补充');
 const review=view.progress(row({percent:null,basis:'requires_revalidation'}));assert.equal(review.percent,null);assert.equal(review.label,'推进项待复核');
 assert.equal(view.progress(row({completed:0,percent:0})).label,'0%');
});
test('缺失/未知/错误类型不展示虚假比例',()=>{
 for(const r of [null,row({basis:'unknown'}),row({percent:'33'}),row({completed:4}),row({requirements_version:true}),row({total:0}),row({basis:'requires_revalidation'}),row({basis:'missing_checklist'})]){
  assert.equal(view.progress(r).available,false);assert.equal(view.progress(r).percent,null);
 }
});
test('三版本拒绝字符串、布尔、空值与缺失，不从旧owner推导责任',()=>{
 const source={version_no:3,requirements_version:2,responsibility_version:1,owner:'执行人'};
 assert.deepEqual(view.versions(source),{version_no:3,requirements_version:2,responsibility_version:1});
 for(const key of ['version_no','requirements_version','responsibility_version'])for(const value of [undefined,null,true,'1',0,-1,1.5])assert.throws(()=>view.versions({...source,[key]:value}));
});

test('outcomes use server terminal reason, distinguish V3 stop and unknown, preserve legacy history',()=>{
 for(const [action,reason,label] of [['close','stopped','已停止'],['submit_completion','completed','正常完成'],['review_completion','completed','正常完成'],[null,'unknown','已结束 · 结果待核对']]){
  const task={status:'completed',execution_mode:'execution_v3',completion_action:action};
  assert.equal(view.outcome(task).endReason,reason);assert.equal(view.summary(task).statusLabel,label);
 }
 for(const [event,reason] of [['cancel','cancelled'],['cancel_task','cancelled'],['reject','rejected'],['decline_task','rejected'],['append_feedback','unknown']])assert.equal(view.outcome({status:'cancelled',last_event_type:event}).endReason,reason);
 const frozen={status:'cancelled',end_reason:'cancelled',last_event_type:'append_feedback',ended_at:'2026-10-26T01:00:00Z'};
 assert.equal(view.outcome(frozen).endReason,'cancelled');assert.equal(view.outcome(frozen).endedAt,frozen.ended_at);
 assert.equal(view.outcome({status:'completed'}).isSuccessful,true);
 assert.equal(view.outcome({status:'needs_adjustment',completion_action:'close'}).isEnded,false);
 assert.equal(view.outcome({status:'completed',execution:{protocol_version:3,completion_action:'close'}}).isSuccessful,false);
});
