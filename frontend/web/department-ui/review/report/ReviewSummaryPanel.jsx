import React from 'react';
import {Alert,Button,Spin,Tag} from 'antd';
import {identity} from '../../pc/data.js';
import {businessErrorMessage} from '../../pc/error-message.js';
import {calendarHint,stable} from '../../work-report/state.js';
import {useReviewReport} from './useReviewReport.js';
import ReviewReportContent from './ReviewReportContent.jsx';
import './review-report.css';
import GenerationNotice from '../../work-report/GenerationNotice.jsx';

function Panel(props){
  const {mode='daily',onOpenDocument}=props,state=useReviewReport(props),{reference,snapshot,run,result}=state;
  const name=mode==='weekly'?'增强周报':'AI 日总结';
  const uncertain=Boolean(reference?.pending && !state.busy);
  const ready=Boolean(snapshot.value?.status==='ready' && !reference?.runId && !state.busy);
  const failed=run.value?.status==='failed' || snapshot.value?.status==='failed';
  const basic=run.value?.result_status==='basic_summary' || result?.revision.origin==='basic_summary';
  const typed=result?.revision.content.review_content;
  const status=state.busy==='prepare' || snapshot.loading || snapshot.value?.status==='preparing'?'正在准备报告材料':state.busy==='generate' || run.value?.status==='queued'?'等待生成':run.value?.status==='running'?'正在生成报告':state.document.loading?'正在读取报告':null;
  return <aside className="rr-panel" aria-label={name} data-testid="review-summary-panel"><header><h2>{name}</h2>{result && <Tag color={basic?'orange':'blue'}>{basic?'基础汇总':result.status==='private'?'私人草稿':'已共享报告'}</Tag>}</header>
    <p className="rr-muted">{mode==='weekly'?'按指标或 KR 回顾目标、进展与风险，普通周报正文仍然保留。':'最多三条事实、影响或风险与下一步建议。'}点击后读取报告材料，完成时保存为私人草稿。</p>
    <p className="rr-muted">{result?.period?.label || snapshot.value?.period?.label || (props.selection?.period_type==='week'?calendarHint('week',props.selection.anchor_date):props.selection?.anchor_date)} · {result?.scope?.label || snapshot.value?.scope?.label || (props.selection?.scope==='team'?'所选管理团队':props.selection?.scope==='member'?'所选成员':props.selection?.scope==='self'?'本人':'所选范围')}</p>
    {state.config.loading && <div className="rr-panel-status"><Spin size="small"/><span>正在读取生成权限与方案</span></div>}
    {state.config.error && <Alert type="error" message={state.config.error.message || '无法读取生成配置'} action={<Button onClick={state.reloadOptions}>重试配置</Button>}/>}
    {state.capabilityError && <Alert type="info" message={state.capabilityError}/>}
    {state.error && <Alert type="error" message={state.error}/>}
    {state.scopeChanged && <Alert type="warning" message="所选范围或授权已变化，请重新读取概览后再生成。" action={props.onReloadOverview?<Button onClick={props.onReloadOverview}>重新读取概览</Button>:null}/>}
    {state.readError && <Alert type="error" message={state.readError.message || '无法读取任务状态'} action={<Button onClick={state.retryRead}>重新读取状态</Button>}/>}
    {uncertain && <Alert type="warning" message="上次提交结果待确认" description="重试会核对上次提交，不会重复创建报告。"/>}
    {state.pending && !state.readError && <div className="rr-panel-status" role="status"><Spin size="small"/><span>{status || '正在读取生成状态'}。可离开页面，稍后回来继续查看。</span></div>}
    {run.value?.status==='running' && run.pollCount>=20 && !state.readError && <Alert type="info" message="报告仍在处理中" description={`已读取生成状态 ${run.pollCount} 次，任务没有超时；可以离开页面，稍后回来继续查看。`} action={<Button onClick={state.retryRead}>重新读取状态</Button>}/>}
    {failed && <Alert type="error" message={businessErrorMessage(run.value?.error || snapshot.value?.error, '报告处理失败，请稍后重试；持续失败请联系管理员')}/>}
    {run.value?.status==='cancelled' && <Alert type="info" message="本次生成已取消"/>}
    {run.value?.result_status==='insufficient_data' && <Alert type="info" message="材料不足，未生成正文"/>}
    {basic && <GenerationNotice run={run.value} document={result}/>}
    {result && !basic && !typed && <Alert type="warning" message="报告已保存，但没有可展示的 AI Review 内容"/>}
    {result && typed && !basic && <ReviewReportContent key={`${result.id}:${result.revision.id}`} compact content={typed} facts={result.review_facts}/>}
    {result && <p className="rr-muted">{result.status==='private'?'已保存为私人草稿，尚未共享。':'此报告已由用户共享。'}重入时会重新校验权限并读取服务器版本。</p>}
    <div className="rr-panel-actions">
      {uncertain || ready?<Button type="primary" aria-label={uncertain?'重试本次提交':'继续生成'} disabled={!state.canGenerate || state.pending || (reference?.pending==='generate' && !ready)} onClick={state.resume}>{uncertain?'重试本次提交':'继续生成'}</Button>:<Button type="primary" aria-label={state.pending?'生成中':result?'重新生成':failed?'重试生成':`生成${name}`} disabled={!state.canGenerate || state.pending || Boolean(state.readError)} loading={state.pending} onClick={state.start}>{state.pending?'生成中':result?'重新生成':failed?'重试生成':`生成${name}`}</Button>}
      {result && onOpenDocument && <Button onClick={()=>onOpenDocument(result.id)}>打开报告草稿</Button>}
    </div>
  </aside>;
}
export default function ReviewSummaryPanel(props){return <Panel key={stable([identity(),props.selection,props.mode || 'daily'])} {...props}/>;}
