import React from 'react';
import {Alert, Button, Drawer} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import {basisLabel} from './profile-history.js';

const count = value => Number.isInteger(value) && value >= 0 ? String(value) : '未知';
const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '未记录';
const evaluationLabels = {assessed:'已评估',partially_assessed:'部分维度已评估',insufficient_data:'材料不足，尚未评估',legacy_unknown:'历史评估口径未知'};
export function EvidenceCoverage({value, coaching, fde = false}) {
  if (!value && !coaching) return null;
  return <div className="ds-pf-coverage">
    {!fde && (value?.status === 'known' ? <>
      <p>纳入 {count(value.used_visits)} / {count(value.total_eligible_visits)} 条正式跟进记录{value.truncated === true ? ` · 仅选取最近 ${count(value.limit)} 条，未覆盖全部材料` : ''}</p>
      {value.window_start && <p className="ds-muted">取材从 {date(value.window_start)} 起，至 {date(value.window_end_exclusive)} 前（北京时间）</p>}
    </> : <p className="ds-muted">历史材料覆盖范围未知，不能确认已使用全部记录。</p>)}
    {fde && value && <p>事实统计：归档记录 {count(value.archived_visits)} 条 · 当前可读 {count(value.readable_archived_visits)} 条 · 协助项目 {count(value.assigned_projects)} 个 · 客户 {count(value.assigned_customers)} 家 · 本人任务 {count(value.owned_tasks)} 项</p>}
    {fde && coaching && <p className="ds-muted">AI 建议材料：{coaching.status === 'legacy_unknown' ? '历史取材范围未知' : `跟进 ${count(coaching.selected_visits)} 条（上限 ${count(coaching.visit_limit)}），任务 ${count(coaching.selected_tasks)} 条（上限 ${count(coaching.task_limit)}）`}{coaching.truncated_text_sources > 0 ? ` · ${count(coaching.truncated_text_sources)} 条正文有截取` : ''}；建议取材不改变上方事实指标的统计范围。</p>}
  </div>;
}

export function ReviewContext({value, onRequest, onEvidence}) {
  if (!value) return null;
  const status = value.currentReview?.status || value.status;
  const busy = Boolean(value.busy || status === 'queued' || status === 'running');
  const group = value.groupCoverage;
  return <section className="ds-pf-review-context" aria-label="评估状态与材料">
    <div className="ds-pf-review-actions">
      <span>{evaluationLabels[value.evaluationState] || '尚无评估结果'}{value.reviewedAt ? ` · 完成于 ${date(value.reviewedAt)}` : ''}</span>
      {value.reviewId && onEvidence && <Button size="small" onClick={()=>onEvidence(value.reviewId)}>查看本次依据</Button>}
      {value.canReview === true && onRequest && <Button size="small" loading={busy} disabled={busy} onClick={onRequest}>更新我的评估</Button>}
    </div>
    {busy && <p className="ds-muted" role="status">{status === 'queued' ? '已受理，等待更新' : '正在更新评估'}{value.reviewId ? '；下方保留上次完成的结果。' : '。'}</p>}
    {(value.error || status === 'failed') && <Alert type="warning" showIcon title={value.error || '本次更新失败'} description={value.reviewId ? '上次完成的结果仍保留。' : undefined}/>}
    {value.evaluationState === 'partially_assessed' && <p className="ds-muted">部分维度材料不足；有总分也不表示六个维度均已评估。</p>}
    <EvidenceCoverage value={value.evidenceCoverage}/>
    {value.scoreBasis && <p className="ds-muted">{basisLabel(value.scoreBasis)} · 评分采用本次保存的规则。</p>}
    {group && <p>范围内有效成员 {count(group.eligible_members)} 人 · 已评估 {count(group.assessed_members)} 人 · 部分评估 {count(group.partially_assessed_members)} 人 · 未评估 {count(group.unassessed_members)} 人 · 历史口径未知 {count(group.legacy_unknown_members)} 人</p>}
    {value.comparisonStatus === 'mixed_basis' && <Alert type="info" title="成员评估规则不一致，暂不合并总分" description="请通过上方成员选择查看个人评估及依据。"/>}
    {value.comparisonStatus === 'legacy_unknown' && <p className="ds-muted">部分成员缺少历史规则记录，暂不进行同口径比较。</p>}
  </section>;
}

const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export function GrowthEvidence({value, onClose, onRetry, onSource}) {
  if (!value?.visible) return null;
  const result = !value.loading && !value.error ? value.result : null;
  const review = result?.review;
  const resultId = review?.id || review?.run_id || review?.review_run_id;
  const ready = result && (!resultId || resultId === value.reviewId);
  const fde = result?.assessment_kind === 'fde_factual_portrait';
  return <Drawer open title="评估依据" size={720} onClose={onClose} destroyOnHidden>
    {value.loading ? <SbStatePanel state="loading" title="正在读取评估依据"/> : value.error ? <SbStatePanel state="error" title={value.error} onRetry={onRetry}/> : !ready ? <SbStatePanel state="empty" title="暂无可展示的评估依据"/> : <div className="ds-pf-evidence">
      <p><b>{result.subject?.name || '所选成员'}</b> · {date(review?.reviewed_at)}{review?.generation_no != null ? ` · 第 ${review.generation_no} 次` : ''}</p>
      {review?.summary && <p>{review.summary}</p>}
      <EvidenceCoverage value={review?.evidence_coverage} coaching={review?.coaching_coverage} fde={fde}/>
      <p className="ds-muted">{basisLabel(review?.score_basis)}</p>
      {review?.score_basis?.snapshot_status === 'frozen' && review.score_basis.score_policy && <details><summary>查看本次评分权重</summary><table className="ds-pf-rule-table"><thead><tr><th>维度</th><th>权重</th></tr></thead><tbody>{(review.score_basis.framework_snapshot?.dimensions || []).map(dim=><tr key={dim.code}><td>{dim.name}</td><td>{review.score_basis.score_policy.definition?.[dim.code] ?? '未记录'}</td></tr>)}</tbody></table></details>}
      {result.evidence_summary && <p>已保存引用 {count(result.evidence_summary.total)} 项 · 当前可读 {count(result.evidence_summary.readable)} 项 · 当前不可读 {count(result.evidence_summary.unavailable)} 项</p>}
      <ol>{(result.evidence || []).map((item,index)=>{
        if (item.readable !== true) return <li key={index} className="ds-muted">当前无法查看该依据。</li>;
        const kind = fde ? item.source_type : 'visit', id = fde ? item.source_id : item.visit_id;
        return <li key={index}>{item.detail || '原记录未保存依据摘要。'}{onSource && ['visit','task'].includes(kind) && uuid(id) && <Button type="link" size="small" onClick={()=>onSource(kind,id)}>查看原{kind === 'visit' ? '跟进' : '任务'}</Button>}</li>;
      })}</ol>
      {!result.evidence?.length && <p className="ds-muted">本次评估未保存可展示的引用。</p>}
    </div>}
  </Drawer>;
}
