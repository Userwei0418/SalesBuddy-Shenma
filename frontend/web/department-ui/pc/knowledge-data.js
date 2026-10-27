// 知识库：方案与定价目录、客户档案 Playbook、方案草稿。
// 目录：企业工作区读部署目录下的 knowledge/catalog.json（运营维护，随 Web 静态文件发布，不新增后端接口）；演示工作区用合成示例。
// Playbook 与方案草稿只用原接口读到的客户、联系人、商机、跟进、任务与风险拼装，并标明出处，不做 AI 推断。
import {SAMPLE_CATALOG} from './knowledge-sample.js';
import {api, amountWan, dateLabel, dayKey, isPreview, loadRisks, normalizeVisit, stageLabel} from './data.js';

const text = value => value == null ? '' : String(value).trim();
const sentence = value => { const t = text(value).replace(/\s+/g, ' '); return t && !/[。！？.!?]$/.test(t) ? `${t}。` : t; };

export function normalizeCatalog(raw) {
  if (!raw || !Array.isArray(raw.items)) return {version: '', updated_at: '', owner: '', categories: [], items: [], sample: false, empty: true};
  const categories = Array.isArray(raw.categories) && raw.categories.length ? raw.categories : [{key: 'solution', label: '解决方案'}, {key: 'pricing', label: '产品定价'}, {key: 'case', label: '案例与资质'}, {key: 'template', label: '模板与话术'}];
  const items = raw.items.filter(item => item && item.id && item.title).map(item => ({...item, status: item.status || 'current', tags: item.tags || [], files: item.files || [], price_items: item.price_items || [],
    highlights: item.highlights || [], scenarios: item.scenarios || [], steps: item.steps || [], deliverables: item.deliverables || []}));
  return {version: raw.version || '', updated_at: raw.updated_at || '', owner: raw.owner || '', categories, items, sample: raw.sample === true, empty: !items.length};
}

export async function loadCatalog() {
  if (isPreview()) return normalizeCatalog(SAMPLE_CATALOG);
  const response = await fetch('knowledge/catalog.json', {cache: 'no-store'});
  if (response.status === 404) return normalizeCatalog(null);
  if (!response.ok) throw Error('方案与定价目录暂时无法读取');
  return normalizeCatalog(await response.json());
}

export async function searchCustomers(query) {
  const keyword = text(query);
  if ([...keyword].length > 100) throw Error('客户关键词不能超过 100 个字');
  // Playbook needs readable customer details. Do not switch to the company
  // reference directory, which also contains customers without detail access.
  const response = await api().listCustomers({q: keyword, pageSize: 50, businessIdentity: 'customer'});
  if (!Array.isArray(response?.items)) throw Error('客户列表暂不可用，请重新搜索');
  return response.items.map(row => ({id: String(row.id), name: text(row.name), owner: text(row.owner_name), industry: text(row.industry_code), level: text(row.level_code)}));
}

async function pages(read, limit = 3) {
  const rows = []; let offset = 0;
  for (let i = 0; i < limit; i++) {
    const response = await read(offset);
    rows.push(...(response?.items || []));
    if (!response?.has_more) return {rows, partial: false};
    offset = Number(response.next_offset) || offset + (response?.items || []).length;
  }
  return {rows, partial: true};
}

export async function loadPlaybook(customerId) {
  const client = api();
  const [overview, contacts, opportunities, visits, tasks, risks] = await Promise.all([
    client.getCustomerOverview(customerId),
    pages(offset => client.listCustomerContacts(customerId, {page_size: 50, offset})).catch(() => ({rows: [], partial: false, error: true})),
    pages(offset => client.listCustomerOpportunities(customerId, {page_size: 50, offset})).catch(() => ({rows: [], partial: false, error: true})),
    pages(offset => client.listVisits({customer_id: customerId, page_size: 50, offset})).catch(() => ({rows: [], partial: false, error: true})),
    client.listDetailTasks({customer_id: customerId, page_size: 50, tab: 'pending'}).catch(() => ({items: []})),
    loadRisks(),
  ]);
  return {overview, contacts, opportunities, visits, tasks: tasks?.items || [], risks: (risks.items || []).filter(r => !r.customerName || r.customerName === text(overview?.name))};
}

const ROLE = {decision_maker: '决策者', influencer: '影响者', user: '使用者'};
const QUADRANT = {main_attack: '主攻区', customer_asset: '客户资产', order_driven: '见单打单', customer_resource: '客户资源'};

export function playbookModel(data, catalog) {
  const c = data.overview || {};
  const visits = data.visits.rows.map(raw => ({...normalizeVisit({customer_name: c.name, ...raw}), raw})).sort((a, b) => b.date.localeCompare(a.date));
  const field = (visit, key) => visit.raw?.[key] ?? visit.raw?.fields?.[key];
  const opportunities = data.opportunities.rows.map(o => ({id: String(o.id), name: text(o.name), stage: stageLabel(o), status: o.status, amount: Number(o.amount) || 0, close: o.expected_close_date || '', productLine: text(o.product_line), owner: text(o.owner_name), probability: o.probability}));
  const open = opportunities.filter(o => o.status === 'open');
  const lines = [...new Set(opportunities.map(o => o.productLine).filter(Boolean))];
  const recommended = (catalog?.items || []).filter(item => item.status === 'current' && item.product_line && lines.includes(item.product_line));
  const needs = visits.map(v => ({date: v.date, needs: text(field(v, 'customer_needs')), budget: text(field(v, 'customer_budget')), business: text(field(v, 'customer_main_business'))})).filter(row => row.needs || row.budget || row.business);
  const contacts = data.contacts.rows.map(row => ({name: text(row.name), title: text(row.title), role: ROLE[row.relationship_role_code] || text(row.relationship_role_code), primary: row.is_primary === true}));
  const lastContactDay = visits.find(v => v.contacts)?.date || '';
  const tasks = data.tasks.map(t => ({id: String(t.id), title: text(t.description) || '任务', due: dayKey(t.due_at), owner: text(t.owner_name || t.assignee_name)}));
  return {
    id: String(c.id || ''), name: text(c.name), owner: text(c.owner_name), team: text(c.team_name), industry: text(c.industry_code), level: text(c.level_code), type: text(c.customer_type_code),
    source: text(c.source_code), partner: text(c.primary_partner_name), address: text(c.address), potential: c.potential_score ?? null, relationship: c.relationship_score ?? null, quadrant: QUADRANT[c.quadrant_code] || '',
    summary: c.summary || null, contacts, lastContactDay, opportunities, open, openAmount: open.reduce((sum, o) => sum + o.amount, 0), visits, needs, tasks, risks: data.risks, recommended,
    partial: data.visits.partial || data.opportunities.partial, nextAction: visits.find(v => v.nextAction)?.nextAction || text(c.attributes?.next_action),
  };
}

export function composePlaybook(model, generatedOn) {
  const lines = [`# ${model.name} · 客户档案 Playbook`, '', `> 生成于 ${generatedOn}，依据系统内客户资料、联系人、商机、跟进记录、任务与风险整理；对外使用前请核对。`, ''];
  lines.push('## 一、客户概况');
  lines.push(`- 行业：${model.industry || '待补充'}；等级：${model.level || '未分级'}；来源：${model.source || '待补充'}；合作伙伴：${model.partner || '无'}`);
  lines.push(`- 负责人：${model.owner || '未分配'}${model.team ? `（${model.team}）` : ''}${model.address ? `；地址：${model.address}` : ''}`);
  if (model.potential !== null || model.relationship !== null) lines.push(`- 客户潜力 ${model.potential ?? '未评估'} · 关系深度 ${model.relationship ?? '未评估'}${model.quadrant ? ` · 所属象限：${model.quadrant}` : ''}`);
  lines.push(`- 在推商机 ${model.open.length} 个，金额合计 ${amountWan(model.openAmount)} 万元；累计跟进 ${model.visits.length} 次${model.visits[0] ? `，最近一次 ${dateLabel(model.visits[0].date)}` : ''}。`, '');
  lines.push('## 二、组织与关键人');
  lines.push(...(model.contacts.length ? model.contacts.map(p => `- ${p.name}${p.title ? ` · ${p.title}` : ''}${p.role ? ` · ${p.role}` : ''}${p.primary ? '（首要联系人）' : ''}`) : ['- 还没有登记联系人，建议在下次拜访后补充决策链。']), '');
  lines.push('## 三、商机全景');
  lines.push(...(model.opportunities.length ? model.opportunities.map(o => `- ${o.name}｜${o.stage || '阶段未填'}｜${amountWan(o.amount)} 万元｜预计关单 ${o.close || '未填写'}｜负责人 ${o.owner || '未填写'}`) : ['- 暂无商机。']), '');
  lines.push('## 四、沟通时间线要点（最近 6 次）');
  lines.push(...(model.visits.length ? model.visits.slice(0, 6).map(v => `- ${v.date} ${v.recorderName}${v.mode ? `（${v.mode}）` : ''}：${sentence(v.content) || '沟通内容未填写。'}${v.nextAction ? `下一步：${sentence(v.nextAction)}` : ''}`) : ['- 还没有跟进记录。']), '');
  if (model.needs.length) {
    lines.push('## 五、已记录的需求与预算');
    lines.push(...model.needs.slice(0, 5).map(n => `- ${n.date}：${[n.business && `主营 ${n.business}`, n.needs && `需求 ${n.needs}`, n.budget && `预算 ${n.budget}`].filter(Boolean).join('；')}`), '');
  }
  lines.push(`## ${model.needs.length ? '六' : '五'}、下一步与待办`);
  const todo = [...(model.nextAction ? [`- 最近约定的下一步：${sentence(model.nextAction)}`] : []), ...model.tasks.slice(0, 8).map(t => `- 任务：${t.title}${t.due ? `（截止 ${t.due}）` : ''}${t.owner ? ` · ${t.owner}` : ''}`)];
  lines.push(...(todo.length ? todo : ['- 暂无待办。']), '');
  lines.push(`## ${model.needs.length ? '七' : '六'}、风险与推荐方案`);
  lines.push(...(model.risks.length ? model.risks.map(r => `- 风险：${r.title}${r.opportunityName ? `（${r.opportunityName}）` : ''}`) : ['- 当前没有待处理的风险。']));
  lines.push(...(model.recommended.length ? model.recommended.map(item => `- 可参考：${item.title} ${item.version || ''}（${item.category === 'pricing' ? '价格表' : '方案'}，更新于 ${item.updated_at || '—'}）`) : ['- 方案库中暂无与该客户产品线匹配的条目。']));
  return lines.join('\n');
}

// 方案草稿：客户背景与需求来自系统记录，方案内容来自所选方案条目，报价来自所选价格表；全部可编辑后再用。
export function composeProposal({model, opportunity, solution, pricing, generatedOn}) {
  // 没有明确方案时不能生成带占位语句的对客草稿；UI 同时会禁用生成按钮，
  // 这里再做一次纯函数层保护，避免其它入口绕过表单校验。
  if (!model || !solution) return '';
  const lines = [`# ${model.name} · ${solution?.title || opportunity?.name || '解决方案'}建议书（草稿）`, '', `> 草稿生成于 ${generatedOn}，由系统按客户档案、跟进记录与方案库拼装；对客发送前请核对事实、口径与报价，报价以审批结果为准。`, ''];
  lines.push('## 一、客户背景');
  lines.push(`${model.name}${model.industry ? `（${model.industry}）` : ''}${model.level ? `，客户等级 ${model.level}` : ''}。${opportunity ? `本次建议对应商机「${opportunity.name}」，当前阶段：${opportunity.stage || '未填写'}${opportunity.close ? `，预计关单 ${opportunity.close}` : ''}。` : ''}`, '');
  lines.push('## 二、需求与现状（来自跟进记录）');
  // 同样的沟通内容只保留最近一条，避免草稿里重复
  const seen = new Set();
  const related = model.visits.filter(v => (!opportunity || v.opportunityIds.includes(opportunity.id)) && v.content && !seen.has(v.content) && seen.add(v.content)).slice(0, 4);
  lines.push(...(related.length ? related.map(v => `- ${v.date}：${sentence(v.content)}${v.goal ? `（拜访目标：${v.goal}）` : ''}`) : ['- 跟进记录中尚未记录明确需求，建议补充后再生成。']));
  for (const n of model.needs.slice(0, 2)) lines.push(`- 已记录需求：${[n.needs, n.budget && `预算 ${n.budget}`].filter(Boolean).join('；')}`);
  lines.push('');
  lines.push('## 三、方案概述');
  lines.push(solution ? sentence(solution.summary) : '（请选择方案条目）');
  if (solution?.highlights?.length) lines.push('', ...solution.highlights.map(h => `- ${h}`));
  lines.push('');
  if (solution?.scenarios?.length) lines.push('## 四、应用场景', ...solution.scenarios.map(item => `- ${item}`), '');
  lines.push(`## ${solution?.scenarios?.length ? '五' : '四'}、实施路径与交付物`);
  lines.push(...(solution?.steps?.length ? solution.steps.map((step, i) => `${i + 1}. ${step}`) : ['- 按试点范围确认 → 配置与对接 → 真实业务试点 → 复盘与推广推进，周期在范围确认后共同制定。']));
  if (solution?.deliverables?.length) lines.push('', `交付物：${solution.deliverables.join('、')}。`);
  lines.push('');
  lines.push(`## ${solution?.scenarios?.length ? '六' : '五'}、商务建议`);
  if (pricing?.price_items?.length) {
    lines.push(`参考《${pricing.title}》（${pricing.version || '当前版本'}${pricing.effective_to ? `，有效期至 ${pricing.effective_to}` : ''}）：`, '', '| 项目 | 计费单位 | 建议价 | 说明 |', '| --- | --- | --- | --- |');
    lines.push(...pricing.price_items.map(p => `| ${p.name} | ${p.unit || ''} | ${p.price || ''} | ${p.note || ''} |`));
    if (pricing.approval) lines.push('', `折扣规则：${pricing.approval}`);
  } else lines.push('- 报价待定：请在「方案与定价」中选择对应价格表，或按审批后的报价填写。');
  lines.push('');
  lines.push(`## ${solution?.scenarios?.length ? '七' : '六'}、下一步`);
  lines.push(`- ${model.nextAction ? sentence(model.nextAction) : '与客户确认试点范围、参与人员与验收口径。'}`);
  lines.push('- 约定试点基线、评估方式与时间范围（不预设提升比例）。');
  return lines.join('\n');
}
