const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const markup=fs.readFileSync(path.join(__dirname,'../miniprogram/pages/task-detail/index.wxml'),'utf8');
test('改期/撤回入口使用当前capability与轮次，禁用提交和读取中操作',()=>{
 for(const [cap,ref,action] of [['recheck_pause','execution.pause.id','recheck'],['withdraw_completion','execution.raw.current_submission','withdrawCompletion']]){const tag=markup.split('\n').find(line=>line.includes(`data-action="${action}"`));assert.ok(tag);assert.ok(tag.includes(`execution.caps.${cap} && ${ref}`));assert.ok(tag.includes('disabled="{{executionBusy || executionLoading}}"'));assert.ok(tag.includes('bindtap="executionPageAction"'));}
});
test('改期含日期时间输入；两种操作说明影响并要求原因，复用确认处理器',()=>{
 assert.ok(markup.includes("executionDialog.action === 'pause' || executionDialog.action === 'recheck_pause'"));for(const text of ['改期原因','撤回原因','到期后不会自动恢复执行','已有成果与完成说明会保留','data-key="executionDate"','data-key="executionTime"','bind:confirm="submitExecutionDialog"'])assert.ok(markup.includes(text),text);
});
test('新版任务有实际操作能力时不显示只读提示，无能力时使用中性说明',()=>{
 const row=markup.split('\n').find(line=>line.includes('class="readonly-tip"'));
 const condition=row.match(/wx:elif="\{\{(.*?)\}\}"/)[1];
 const visible=execution=>require('node:vm').runInNewContext(condition,{execution});
 assert.equal(visible(null),true);assert.equal(visible({caps:{}}),true);
 for(const cap of ['append_feedback','set_item_progress','pause','resume','propose_requirements','review_completion','respond_reviewer_handover'])assert.equal(visible({caps:{[cap]:true}}),false,cap);
 assert.ok(row.includes('当前暂无可执行操作，可查看任务进展与记录。'));
});
