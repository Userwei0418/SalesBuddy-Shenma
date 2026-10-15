import React from 'react';
import {Avatar, Button, Drawer, Tooltip} from 'antd';
import {NavIcon} from './icons.jsx';
import {LoadState, SourceTag, Tag2, useAsync} from './ui.jsx';
import {dateLabel, open, relativeDay} from './data.js';
import {loadFollowupDetail} from './visit-records.js';

// 小头像只放一个字（姓），22px 里两个字会被压得看不清
export const initials = name => String(name || '').replace(/\s/g, '').slice(0, 1) || '—';

export function VisitOpportunityLinks({visit}) {
  return visit.opportunityIds.map(id => {
    const name = visit.opportunityNames?.[id] || (visit.opportunityIds.length === 1 ? visit.opportunityName : '') || '关联商机（名称未记录）';
    const allowed = visit.opportunityAccess?.[id] ?? (id === visit.opportunityId && visit.canOpenOpportunity);
    return allowed ? <button key={id} type="button" className="pc-link pc-fu-opportunity" onClick={() => open.opportunity(visit.opportunityCustomers?.[id]?.id || (id === visit.opportunityId ? visit.customerId : ''), id)}>{name}</button> : <span key={id}>{name}</span>;
  });
}

// 一条跟进：记录人、时间、客户 · 商机（可点开客户时间轴 / 商机详情）、沟通要点与下一步。
export function FollowupItem({visit, onSelect, showPerson = true, dense}) {
  return <article className={`pc-fu${dense ? ' is-dense' : ''}`}>
    <div className="pc-fu-head">
      {showPerson && <Avatar size={22} className="pc-fu-avatar">{initials(visit.recorderName)}</Avatar>}
      {showPerson && <b className="pc-fu-person">{visit.recorderName}</b>}
      <span className="pc-fu-time" title={visit.at || visit.date}>{relativeDay(visit.date)}{visit.mode ? ` · ${visit.mode}` : ''}</span>
      <SourceTag source={visit.source} />
      {visit.isFirst && <Tag2 tone="primary">首次拜访</Tag2>}
      {visit.statusLabel && visit.status !== 'archived' && <Tag2>{visit.statusLabel}</Tag2>}
    </div>
    <div className="pc-fu-target">
      {visit.customerId && visit.canOpenCustomer !== false ? <button type="button" className="pc-link" onClick={() => open.customer(visit.customerId)} title="打开客户档案，查看完整跟进时间轴">{visit.customerName}</button> : <span>{visit.customerName}</span>}
      {!!visit.opportunityIds.length && <><span className="pc-fu-dot" aria-hidden="true">·</span><VisitOpportunityLinks visit={visit} /></>}
    </div>
    {visit.content && <p className="pc-fu-content pc-clamp-2">{visit.content}</p>}
    {visit.nextAction && <p className="pc-fu-next"><span>下一步</span>{visit.nextAction}</p>}
    {onSelect && <Button type="link" size="small" className="pc-fu-more" onClick={() => onSelect(visit)}>详情</Button>}
  </article>;
}

// 按日期分组的跟进流
export function FollowupStream({visits, onSelect, limit, showPerson = true, emptyText = '本期还没有跟进记录'}) {
  const rows = limit ? visits.slice(0, limit) : visits;
  if (!rows.length) return <p className="pc-fu-empty">{emptyText}</p>;
  let day = '';
  return <div className="pc-fu-stream">
    {rows.map(visit => {
      const header = visit.date !== day ? (day = visit.date, <div className="pc-fu-day" key={'d' + visit.date}>{dateLabel(visit.date) || '日期未填写'}</div>) : null;
      return <React.Fragment key={visit.id}>{header}<FollowupItem visit={visit} onSelect={onSelect} showPerson={showPerson} /></React.Fragment>;
    })}
  </div>;
}

// 跟进详情抽屉：完整字段 + 去客户时间轴 / 拜访详情
export function FollowupDrawer({visit: selected, onClose}) {
  const result = useAsync(signal => selected ? loadFollowupDetail(selected, signal) : null, [selected]);
  const visit = result.value;
  const rows = visit ? [
    ['跟进日期', dateLabel(visit.date)], ['记录人', visit.recorderName], ['客户', visit.customerName], ['关联商机', visit.opportunityName || '不关联商机'],
    ['沟通方式', visit.mode], ['对接人', visit.contacts], ['协助 FDE', visit.fdeNames.join('、')], ['协同人', visit.collaborators.join('、')],
    ['拜访目标', visit.goal], ['达成情况', visit.expectation], ['时长', visit.duration ? `${visit.duration} 分钟` : ''], ['状态', visit.statusLabel],
  ].filter(([, value]) => value) : [];
  return <Drawer open={Boolean(selected)} onClose={onClose} size={560} title={visit ? `${visit.customerName} · 跟进记录` : '跟进记录详情'} rootClassName="department-ui pc-fu-drawer"
    extra={visit && <div className="pc-fu-drawer-actions">
      {visit.customerId && visit.canOpenCustomer !== false && <Tooltip title="客户档案里有这家客户完整的跟进时间轴"><Button size="small" icon={<NavIcon name="clock" />} onClick={() => { onClose(); open.customer(visit.customerId); }}>客户时间轴</Button></Tooltip>}
      {visit.canOpen && <Button size="small" type="primary" onClick={() => { onClose(); open.visit(visit.id, visit.customerId); }}>拜访详情</Button>}
    </div>}>
    <LoadState loading={result.loading} error={result.error} onRetry={result.reload}>{visit && <div className="pc-fu-detail">
      <div className="pc-fu-detail-tags"><SourceTag source={visit.source} />{visit.isFirst && <Tag2 tone="primary">首次拜访</Tag2>}</div>
      <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <section><h3>沟通内容</h3><p>{visit.content || '未填写'}</p></section>
      <section><h3>下一步计划</h3><p>{visit.nextAction || '未填写'}</p></section>
    </div>}</LoadState>
  </Drawer>;
}
