import React from 'react';
import {Button, Table} from 'antd';
import {SbSegmented} from '@shandiant/ui-react';
import {invokeAdvice, resolveComponent} from './advice-bridge.js';
import './workflow-completion.css';

// Adapted from the approved 1.1.0-ui.2 package; business actions remain in source/miniprogram.
export function AdviceActions({page, advice, suggestion, invokeOn}) {
  const s = suggestion;
  const note = text => <p className="ds-muted ds-advice-note" role="status">{text}</p>;
  if (advice.checking || advice.needsCheck) return note(advice.checkError || '正在核对建议有效性，请检查建议更新后再处理。');
  const instance = resolveComponent(page, {selector: 'advice-actions', analysisId: advice.id, suggestionId: s.id});
  const run = (action, payload) => invokeAdvice(page, advice, s, invokeOn, action, payload);
  if (s.decision === 'adopted') {
    const tasks = Array.isArray(s.tasks) ? s.tasks : s.task_id ? [{id:s.task_id}] : [];
    return <div className="ds-advice-actions"><strong>已采纳{tasks.length > 1 ? ' · 每人独立待办' : ''}</strong>
      {tasks.map(task => <Button key={task.id} size="small" type="link" disabled={!instance} onClick={() => run('openTask', {dataset:{id:task.id}})}>{task.assignee_name || '查看待办'}{task.assignee_account ? `（${task.assignee_account}）` : ''} · 查看任务</Button>)}
      {!tasks.length && note('当前无可查看的待办')}</div>;
  }
  if (s.decision !== 'pending') return note(`已处理 · 无需待办${s.decision_note ? ` · ${s.decision_note}` : ''}`);
  if (advice.stale) return note('建议已失效，请重新获取后再决定是否创建待办。');
  if (!instance?.data.canDecide) return note(instance ? '建议供协作参考，由有权负责人决定。' : '正在准备建议操作…');
  return <div className="ds-advice-actions"><Button type="primary" size="small" disabled={instance.data.saving} onClick={() => run('adopt')}>采纳并建待办</Button><Button size="small" loading={instance.data.saving} disabled={instance.data.saving} onClick={() => run('dismiss')}>无需待办</Button></div>;
}

export function RecordingGuide({data:d, invoke}) {
  return <div className="ds-recording-guide">
    <Button type="link" aria-expanded={Boolean(d.recordingGuideVisible)} aria-controls="ds-recording-guide-body" onClick={() => invoke('toggleRecordingGuide')}>{d.recordingGuideVisible ? '收起录音指引' : '录音时可以说些什么'}</Button>
    {d.recordingGuideVisible && <div id="ds-recording-guide-body">
      <h3>每次跟进</h3><p>背景：客户名称、实际沟通日期、对接人姓名。</p><p>结果：讨论了什么、客户如何反馈、已达成什么结果。</p><p>下一步：具体哪天、由谁负责、做什么或达成什么目标。</p>
      {(d.canFirstVisit || d.isFirstVisit) && <><h3>首次拜访时</h3><p>补充客户主营业务、需求、预算和联系人角色，未说清的内容可在确认页补充。</p></>}
      {d.canCreateOpportunity && <><h3>本次产生新商机时</h3><p>说明项目名称、阶段或赢单概率、ACV 金额及单位、预计关单日期、直销或合作伙伴。</p><p>已有季度确收和回款计划可一起说明；跟进已有商机时说明商机名称，稍后核对关联。</p></>}
      <p className="ds-muted">只记录已知事实，不确定的信息先留空。转写后核对文字，并在确认页补齐信息。</p>
    </div>}
  </div>;
}

export function PresentationSwitch({data, invoke}) {
  return data.presentationOptions?.length > 1 ? <div className="ds-presentation-switch"><SbSegmented aria-label="业务视角" value={data.presentation} options={data.presentationOptions.map(option => ({value:option.value,label:option.label}))} onChange={kind => invoke('changePresentation', {dataset:{kind}})} /></div> : null;
}

export function RankingMembers({row}) {
  const members = row.members || [];
  return <div className="ds-ranking-members"><p>{row.memberSummary}</p><p className="ds-muted">零次成员计入人均分母；个人次数按本团队的跟进记录统计。</p>
    <Table size="small" rowKey="user_id" pagination={false} scroll={{x:460}} dataSource={members} locale={{emptyText:'暂无有效业务成员，该团队不参与人均排名'}} columns={[
      {title:'成员',key:'member',render:(_,member)=><span>{member.name || member.display_name}<small className="ds-muted">{member.account_code}</small></span>},
      {title:'跟进次数',key:'count',render:(_,member)=>member.followup_count == null ? '未取得' : `${member.followup_count} 次`},
      {title:'统计说明',key:'scope',render:(_,member)=><span>{member.customer_count == null ? '客户数未取得' : `${member.customer_count} 家客户`}<small className="ds-muted">{member.current_member === true ? '计入人均分母' : member.current_member === false ? '原团队贡献，不计入当前人数' : '成员归属待核对'}</small></span>},
    ]} />
  </div>;
}
