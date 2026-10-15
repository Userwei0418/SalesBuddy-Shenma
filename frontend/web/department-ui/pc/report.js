// 工作报告正文：只把系统里已录入的跟进、商机、任务、风险按固定结构排出来，不做推断、不生成承诺。
// 纯函数，单元测试见 tests/pc-report.test.mjs。
const clean = value => typeof value === 'string' ? value.replace(/\r\n?/g, '\n').replace(/[\t ]+/g, ' ').trim() : '';
const sentence = value => { const text = clean(value); return text && !/[。！？.!?]$/.test(text) ? `${text}。` : text; };
const md = value => clean(value).replace(/\n+/g, ' ');
const short = key => { if (!key) return ''; const [, m, d] = key.split('-').map(Number); return `${m}/${d}`; };
const wan = value => { const n = value == null || value === '' ? NaN : Number(value) / 10000; return Number.isFinite(n) ? (n >= 100 ? Math.round(n) : Math.round(n * 10) / 10) + ' 万' : '金额未填'; };
const REPORT_LABEL = Object.freeze({day: '日报', week: '周报', biweek: '双周报', month: '月报', quarter: '季度报'});

export function composeWorkReport({kind, range, scopeName, author, summary, members = [], opportunities = [], newOpportunities = [], completedTasks = [], openTasks = null, risks = [], riskTotal = risks.length, generatedAt = ''}) {
  const title = `${scopeName}${REPORT_LABEL[kind] || '工作报告'}（${range.title}）`;
  const lines = [`# ${title}`, ''];
  if (author || generatedAt) lines.push(`> ${[author ? `整理：${author}` : '', generatedAt ? `数据截至 ${generatedAt}` : ''].filter(Boolean).join(' · ')}`, '');
  lines.push('## 一、概览');
  lines.push(`- 跟进 ${summary.visits} 条，覆盖客户 ${summary.customers} 家、商机 ${summary.opportunities} 个${summary.presales ? `（含售前 / FDE 跟进 ${summary.presales} 条）` : ''}。`);
  if (summary.members > 1) lines.push(`- 活跃成员 ${summary.activeMembers}/${summary.members} 人，人均跟进 ${summary.perCapita ?? 0} 条。`);
  lines.push(`- 新增商机 ${newOpportunities.length} 个；完成任务 ${completedTasks.length} 项${openTasks ? `；待办 ${openTasks.total} 项，其中逾期 ${openTasks.counts?.overdue ?? openTasks.overdue.length} 项` : ''}；待处理风险 ${riskTotal} 项。`, '');

  lines.push('## 二、成员进展');
  const active = members.filter(m => m.count), idle = members.filter(m => !m.count && !m.outside);
  if (!active.length) lines.push('- 本期还没有跟进记录。');
  for (const member of active) {
    lines.push(`### ${member.name}（${member.count} 条 · 客户 ${member.customerCount} · 商机 ${member.opportunityCount}）`);
    for (const visit of member.visits) {
      const target = [visit.customerName, visit.opportunityName].filter(Boolean).join(' · ');
      lines.push(`- ${short(visit.date)} ${md(target)}：${md(sentence(visit.content) || '沟通内容未填写。')}${visit.nextAction ? `下一步：${md(sentence(visit.nextAction))}` : '（未写下一步）'}`);
    }
    lines.push('');
  }
  if (idle.length) lines.push(`本期未跟进：${idle.map(m => m.name).join('、')}。`, '');

  lines.push('## 三、重点商机进展');
  if (!opportunities.length) lines.push('- 本期没有有跟进记录的商机。');
  for (const o of [...opportunities].sort((a, b) => b.amount - a.amount).slice(0, 12)) {
    lines.push(`- ${md(o.customerName)} · ${md(o.name)}｜${o.stage || '阶段未填'}｜${wan(o.amount)}｜预计关单 ${o.close || '未填写'}${o.lastVisit ? `｜最近：${short(o.lastVisit.date)} ${md(o.lastVisit.recorderName)}，${md(sentence(o.lastVisit.content))}` : ''}`);
  }
  if (newOpportunities.length) {
    lines.push('', `新增商机：${newOpportunities.map(o => `${md(o.customerName)} · ${md(o.name)}（${wan(o.amount)}，${o.owner || '负责人未填'}）`).join('；')}。`);
  }
  lines.push('');

  lines.push('## 四、下一步与待协调');
  const next = active.flatMap(m => m.visits.filter(v => v.nextAction).map(v => `- ${m.name}：${md(v.customerName)}，${md(sentence(v.nextAction))}`));
  lines.push(...(next.length ? next : ['- 跟进记录里还没有填写下一步。']));
  if (openTasks?.overdue?.length) lines.push('', `逾期任务：${openTasks.overdue.slice(0, 10).map(t => `${md(t.title)}（${t.owner || '负责人未填'}）`).join('；')}${openTasks.overdue.length > 10 ? ` 等 ${openTasks.overdue.length} 项` : ''}。`);
  if (completedTasks.length) lines.push('', `已完成：${completedTasks.slice(0, 10).map(t => md(t.title)).join('；')}${completedTasks.length > 10 ? ` 等 ${completedTasks.length} 项` : ''}。`);
  lines.push('');

  lines.push('## 五、风险');
  lines.push(...(risks.length ? risks.slice(0, 10).map(r => `- ${md(r.title)}${r.customerName ? `（${md(r.customerName)}${r.opportunityName ? ' · ' + md(r.opportunityName) : ''}）` : ''}`) : ['- 没有待处理的风险。']));
  lines.push('', '—— 本报告按系统已录入的跟进、商机、任务与风险汇总，未做 AI 推断；以系统记录为准。');
  return {title, text: lines.join('\n')};
}
