import React from 'react';
import {Alert,Button} from 'antd';

// A saved meeting board also has origin=basic_summary, but did not attempt AI.
// Only a real generation result/run (or the service's safe issue) implies fallback.
export function basicGeneration({run,document}={}) {
  return run?.result_status==='basic_summary' || Boolean(document?.generation_issue) ||
    Boolean(document?.revision?.origin==='basic_summary' && document.revision.run_id);
}
export function generationIssueText(issue) {
  const reasons={
    AI_OUTPUT_INVALID:'AI 返回内容未通过格式校验。',
    AI_SOURCE_REFERENCES_MISSING:'AI 返回内容缺少可核对的来源引用。',
    AI_PROVIDER_UNAVAILABLE:'AI 服务本次未能完成生成。',
    AI_GENERATION_TIMEOUT:'AI 处理超过本次等待时间。',
    AI_RESULT_UNAVAILABLE:'本次 AI 未完成生成，历史记录未保留可确认的具体原因。',
  };
  return reasons[issue?.code] || '本次 AI 未完成生成，已保留可核对的基础材料汇总。';
}
export default function GenerationNotice({run,document,onRetry,busy}) {
  if(!basicGeneration({run,document}))return null;
  const issue=document?.generation_issue || run?.generation_issue;
  return <Alert type="warning" showIcon title="AI未完成，仅基础汇总"
    description={<><p>{generationIssueText(issue)}</p><p>当前草稿已保存，可以继续保留、编辑或核对内容。重新生成不会自动共享。</p></>}
    action={onRetry && issue?.retryable!==false && <Button disabled={busy} onClick={onRetry}>重新生成 AI 报告</Button>}/>;
}
