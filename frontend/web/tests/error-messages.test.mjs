import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {businessErrorMessage} from '../department-ui/pc/error-message.js';
const module = {exports:{}};
vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/errorMessages.js', import.meta.url),'utf8'), {module});
const messages = module.exports;

test('Web asynchronous failure renderer ignores raw provider detail and resolves registered codes', () => {
  globalThis.SalesRuntime = {require:name=>{
    assert.equal(name,'utils/errorMessages');return messages;
  }};
  assert.equal(businessErrorMessage({code:'RUN_FAILED',detail:'field required: private_customer_id'}),'智能处理未完成，请检查输入后重试');
  assert.equal(businessErrorMessage({code:'UNKNOWN_WORKER_ERROR',detail:'私有字段错误 password SQL'},'报告准备失败，请重新读取状态'),'报告准备失败，请重新读取状态');
  const reportFallback='报告处理失败，请稍后重试；持续失败请联系管理员';
  assert.equal(businessErrorMessage({code:'WORK_REPORT_JOB_FAILED',message:'permission denied for table private_table',detail:'SQL worker diagnostics'},reportFallback),'报告材料准备或生成失败，请稍后重试；持续失败请联系管理员');
  assert.equal(businessErrorMessage({code:'UNKNOWN_REPORT_FAILURE',message:'SQL worker diagnostics'},reportFallback),reportFallback);
  assert.equal(businessErrorMessage({code:'AI_SERVICE_NOT_CONFIGURED'}),'智能服务尚未配置，请联系管理员');
  delete globalThis.SalesRuntime;
  assert.equal(businessErrorMessage({detail:'private field'},'页面未能打开，请刷新'),'页面未能打开，请刷新');
});

test('Web shared request mapping retains legacy code parsing and hides unknown validation fields', () => {
  assert.equal(messages.codeFor({detail:{code:'TASK_EXECUTION_FORBIDDEN'}}),'TASK_EXECUTION_FORBIDDEN');
  assert.equal(messages.messageFor({detail:'INTERNAL_ERROR'},500),'服务暂时异常，请稍后重试');
  assert.equal(messages.messageFor({detail:[{loc:['body','private_field'],type:'missing',msg:'private input'}]},422),'请检查填写内容后重试');
  assert.equal(messages.messageFor({code:'OPPORTUNITY_TRADE_RELATION_REQUIRED',message:'SQL field required'},422),'请先在交易物料中选择交易关系，再保存实际回款');
  assert.equal(messages.messageFor({detail:[{loc:['body','collection_actuals',0,'source_ref'],type:'missing',msg:'private input'}]},422),'请填写回款依据');
});
