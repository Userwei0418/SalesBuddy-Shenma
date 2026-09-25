const { statusLight, taskLight } = require('./statusLight');
const { opportunitySignal, visitSignal } = require('./customerSignals');
const { QUADRANTS } = require("./quadrant");
const { normalizeCustomerLevel } = require("./customerLevel");
const { buildCustomerRadar } = require('./customerRadar');
const { activeWorkspaceId, displayText } = require('./demoDisplay');
const { businessTimeLabel } = require('./customerAdvice');
const { expectedCloseQuarter } = require('./opportunityQuarter');

const QUADRANT_NAME = {
  main_attack: "主攻区",
  customer_asset: "客户资产",
  order_driven: "见单打单",
  customer_resource: "客户资源",
};

const opp = require('./opportunity');
const stageName = value => (opp.STAGES.find(s=>s.code===value)||{}).label;

const RELATIONSHIP_ROLE_NAME = {
  decision_maker: "决策者", influencer: "影响者", user: "使用者",
  决策者: "决策者", 影响者: "影响者", 使用者: "使用者",
};

const INTERACTION_MODE_NAME = {
  offline_meeting: "线下会议", online_meeting: "线上会议",
  phone_voice: "电话/语音", social_meal: "饭局/聚会",
};

const EXPECTATION_NAME = {
  exceeded: "超出100%", met: "达成100%", met_50_100: "达成50-100%",
  met_30_50: "达成30-50%", met_10_30: "达成10-30%", not_met: "未达成",
};

const CUSTOMER_TYPE_NAME = { prospect:"潜在客户", opportunity:"商机客户", won:"已成单客户" };

const CUSTOMER_STAGE_NAME = {
  lead: "待分配",
  prospect: "潜在客户",
  active: "持续跟进",
  dormant: "暂缓跟进",
  won: "已成交",
  lost: "已流失",
  archived: "已归档",
};

function contactRoleText(contact = {}) {
  const explicit = RELATIONSHIP_ROLE_NAME[contact.relationship_role_code];
  if (explicit) return explicit;
  return "角色待补充";
}

function opportunityStageText(value) {
  return stageName(value) || value || "暂无商机";
}

function opportunityProbability(item = {}) { return opp.stageOf(item).probability; }

function amountText(value) {
  const amount = Number(value || 0);
  if (!amount) return "0元";
  if (amount >= 10000) return `${(amount / 10000).toFixed(amount % 10000 ? 1 : 0)}万`;
  return `${amount.toFixed(0)}元`;
}

function dateText(value) {
  if (!value) return "待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function expectedCloseText(item = {}) {
  if (item.expected_close_date) return dateText(item.expected_close_date);
  const quarter = expectedCloseQuarter(item);
  return quarter ? `${quarter.year} Q${quarter.quarter}` : '待确认';
}

function savedText(value) { return typeof value === 'string' ? value.trim() : ''; }

function opportunityPartnerFields(item) {
  const names = (Array.isArray(item.associated_partners) ? item.associated_partners : [])
    .map(partner => savedText(partner && partner.name)).filter(Boolean);
  const resale = savedText(item.partner_name);
  return {
    associatedPartnersText: [...new Set(names)].join('、') || '未填写',
    resalePartnerText: resale && !(item.sales_channel === 'direct' && resale === '直销') ? resale : '未填写',
    signingMethodText: ({direct:'客户直签',partner:'伙伴转售'})[item.sales_channel] || '未填写',
  };
}

function historicalPeriodRecords(item) {
  return (Array.isArray(item.historical_period_actuals) ? item.historical_period_actuals : [])
    .filter(record => record && typeof record === 'object').map((record, index) => {
      // These are saved source values, not confirmed dated actuals or forecasts.
      // Do not round, convert yuan/wan, infer tax, or replace a missing amount with 0.
      const rawAmount = typeof record.raw_amount === 'number' && Number.isFinite(record.raw_amount)
        ? String(record.raw_amount) : savedText(record.raw_amount);
      const unit = ({wan_cny:'万元',cny:'元'})[record.source_unit] || '（单位未填写）';
      const period = Number.isInteger(record.year) && record.year > 0 && [1,2,3,4].includes(record.quarter)
        ? `${record.year} Q${record.quarter}` : '季度未填写';
      return {
        ...record,key:`history-${index}`,period,
        kindText: ({recognized:'确收',collection:'回款'})[record.kind] || '类型未填写',
        amountText: rawAmount ? `${rawAmount}${unit}` : '未填写',
        taxBasisText: ({unknown:'含税口径未知',inclusive:'含税',exclusive:'不含税',not_applicable:'税口径不适用'})[record.tax_basis] || '含税口径未知',
        sourceFieldText: savedText(record.source_field),
      };
    });
}

function dateTimeText(value) {
  if (!value) return "时间待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function visitTimeValue(value) {
  if (!value) return 0;
  const text = String(value).trim();
  let normalized = text;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) normalized = `${text}T00:00:00+08:00`;
  else if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text)) normalized = `${text.replace(" ", "T")}+08:00`;
  const timestamp = new Date(normalized).getTime();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function sortVisitsByTime(visits = []) {
  return [...visits].sort((a, b) => {
    const primary = visitTimeValue(b.interaction_at || b.visit_date) - visitTimeValue(a.interaction_at || a.visit_date);
    if (primary) return primary;
    const created = visitTimeValue(b.created_date || b.created_at) - visitTimeValue(a.created_date || a.created_at);
    if (created) return created;
    return String(b.id || "").localeCompare(String(a.id || ""));
  });
}

function normalizeCustomerTask(item = {}) {
  const dueAt = item.due_at ? new Date(item.due_at).getTime() : 0;
  const overdue = Boolean(dueAt && dueAt < Date.now() && item.status !== "completed" && item.status !== "cancelled");
  const statusMap = {
    pending_confirm: "待接受",
    pending_review: "待发起人确认", pending_execution: "已接受",
    in_progress: "进行中",
    completed: "已完成",
    deferred: "已延期",
    cancelled: "已拒绝",
  };
  let statusTone = "pending";
  if (item.status === "completed") statusTone = "completed";
  else if (item.status === "cancelled") statusTone = "cancelled";
  else if (overdue) statusTone = "overdue";
  return {
    ...item,
    signal:taskLight(item),
    statusLabel: overdue ? "已逾期" : (statusMap[item.status] || "待完成"),
    statusTone,
    statusSymbol: item.status === "completed" ? "✓" : "×",
    timelineAt: dateTimeText(item.completed_at || item.due_at || item.created_at),
    assigneeName: item.assignee_name || "负责人待确认",
    opportunityName: item.opportunity_name || "",
    opportunityId: item.opportunity_id || "",
  };
}

function mapScore(value) {
  if (!['number', 'string'].includes(typeof value) || String(value).trim() === '') return null;
  const score = Number(value);
  return Number.isFinite(score) && score >= 0 && score <= 100 ? score : null;
}

function normalizeCustomerSummary(item) {
  const potential = mapScore(item.potential_score !== undefined ? item.potential_score : item.potential);
  const relationship = mapScore(item.relationship_score !== undefined ? item.relationship_score : item.relationship);
  const hasMapScores = potential !== null && relationship !== null;
  const quadrant = hasMapScores ? (QUADRANT_NAME[item.quadrant_code] || item.quadrant || "待计算") : "待评估";
  const planPeriods = Array.isArray(item.plan_close_periods) ? item.plan_close_periods :
    (item.plan_close_dates || []).map(date=>({date}));
  return {
    ...item,
    plan_close_periods: planPeriods.map(period=>({date:period && period.date || null,
      year:period && period.year != null ? period.year : null,quarter:period && period.quarter != null ? period.quarter : null})),
    owner: item.owner_name || item.owner || "待分配",
    team: item.team_name || item.team || "待分配团队",
    level: normalizeCustomerLevel(item.level_code || item.level),
    potential, relationship, hasMapScores,
    potentialText: potential === null ? '待评估' : String(potential),
    relationshipText: relationship === null ? '待评估' : String(relationship),
    quadrant,
    signal:statusLight(item.risk_title || (item.risk && item.risk !== '暂无重大风险') ? 'yellow' : 'green',item.risk_title || item.risk || '当前未发现风险'),
    risk: item.risk_title || item.risk || "暂无重大风险",
    latestVisitAt: item.latest_visit_at || null,
    lastVisit: item.latest_visit_at ? dateText(item.latest_visit_at) : "暂无拜访",
    opportunityName: item.opportunity_name || "暂无活跃商机",
    estimatedAmount: item.opportunity_amount == null ? "—" : amountText(item.opportunity_amount),
    mapAmount: item.opportunity_amount == null ? null : Number(item.opportunity_amount),
    customerStageCode: item.lifecycle_status || "prospect",
    customerStage: CUSTOMER_STAGE_NAME[item.lifecycle_status] || item.lifecycle_status || "潜在客户",
    weeklyFollowUps: Number(item.weekly_follow_up_count || 0),
    opportunityStageCode: item.opportunity_stage || "",
  };
}

// BACKEND-CONTRACT overview 的 summary/profile 为全量授权统计；列表为独立分页，不以已加载条数重算总量。
// 本函数只绘制后端 profile 六维指标；缺项不补零或推算健康分。annualValue 实为 open 商机 ACV，不是年度收入。
// 缺失 stage/probability 目前 stageOf 回退 10%；quarterly_forecasts 缺字段与 null 的显示不同，后端应明确返回 null。
// 详见 docs/backend-handoff/客户与商机详解.md 的读模型、评分和现状差异。
function normalizeCustomerDetail(raw, selectedOpportunityId = "", {preserveVisitOrder = false} = {}) {
  if (!raw) return null;
  const workspaceId = activeWorkspaceId();
  const customer = normalizeCustomerSummary(raw);
  const opportunities = Array.isArray(raw.opportunities) ? raw.opportunities : [];
  const selectedOpportunity = selectedOpportunityId
    ? opportunities.find((item) => String(item.id) === String(selectedOpportunityId))
    : null;
  const summary = raw.summary;
  const summaryPending = raw.read_model === 'detail_header_v1' && !summary;
  const boundedDetail = ['detail_header_v1','detail_overview_v1'].includes(raw.read_model);
  const taskStatusCounts = summary && summary.task_status_counts || {};
  const primaryOpportunity = selectedOpportunity || raw.primary_opportunity || opportunities.find((item) => item.status === "open") || opportunities[0] || {};
  const primaryProbability = opportunityProbability(primaryOpportunity);
  const stages = opp.STAGES.slice(0,6).map(s => s.label);
  const normalizedOpportunities = opportunities.map((item, index) => {
    const stage = opportunityStageText(item.stage_code);
    const currentStageIndex = Math.max(0, stages.indexOf(stage));
    return {
      ...item,
      ...opportunityPartnerFields(item),
      historicalPeriodRecords: historicalPeriodRecords(item),
      signal: boundedDetail && !item.risk_summary ? statusLight('gray','商机风险待评估') : opportunitySignal({...item,probability:opportunityProbability(item)},raw.risks || []),
      anchorId: `opportunity-card-${index}`,
      isFocused: Boolean(selectedOpportunityId) && String(item.id) === String(selectedOpportunityId),
      stage,
      stageText: opp.stageOf(item).text,
      quarterDetails: (item.quarterly_forecasts || []).map(q => ({ label: `${q.year} Q${q.quarter}`, recognized: q.recognized_amount == null ? "未填写" : amountText(q.recognized_amount), collection: q.collection_amount == null ? "未填写" : amountText(q.collection_amount),
        collectionConfidenceText: ({high:'高（≥70%）',low:'低（<70%）'})[q.collection_confidence] || '' })),
      originalOwnerName: typeof item.original_owner_name === 'string' ? item.original_owner_name.trim() : '',
      ownershipResolutionText: ({confirmed:'已确认',provisional:'暂定归属',unassigned:'待分配'})[item.ownership_resolution] || '',
      grade: opp.gradeOfAmount(item.amount),
      amount: item.amount == null || Number(item.amount)<0 ? "—" : amountText(item.amount),
      probability: opportunityProbability(item),
      expectedDate: expectedCloseText(item),
      stageSteps: stages.map((label, index) => ({
        label,
        status: item.status === "lost" ? "upcoming" : index < currentStageIndex ? "done" : index === currentStageIndex ? "current" : "upcoming",
      })),
    };
  });
  const visitDates = require('./visitDates');
  // Paged histories retain the server's order, including timestamp/ID ties across pages.
  const visitRows = preserveVisitOrder ? (raw.visits || []) : sortVisitsByTime(raw.visits || []);
  const visits = visitRows.map((visit) => {
    const fields = visit.fields || {};
    const value = (key) => visit[key] ?? fields[key];
    const isFirstVisit = [true, 1, "1", "true"].includes(value("is_first_visit"));
    // Only saved visit display fields are projected; the original visit stays intact.
    const text = value => displayText(value, ['confirmed', 'archived'].includes(visit.status) ? workspaceId : undefined);
    const originalRecorderName = typeof visit.original_recorder_name === 'string' ? text(visit.original_recorder_name.trim()) : '';
    const managerName = typeof visit.manager_name === 'string' ? text(visit.manager_name.trim()) : '';
    const visitCustomerId = visit.customer_id || '';
    const links = Array.isArray(visit.linked_opportunities) && visit.linked_opportunities.length ? visit.linked_opportunities :
      (visit.opportunity_id ? [{id:visit.opportunity_id,name:visit.opportunity_name || '商机名称待补充'}] : []);
    const linkedOpportunities = [...new Map(links.filter(link=>link && typeof link.id==='string' && link.id.trim()).map(link=>[link.id.trim(),{id:link.id.trim(),name:text(link.name || '商机名称待补充')}])).values()];
    return {
    id: visit.id,
    customerId: visitCustomerId,
    partnerId: visit.partner_id || '',
    originalRecorderName,managerName,linkedOpportunities,
    isSummary: Boolean(visit.is_summary),
    fdeParticipants:visit.fde_participants||[],
    fdeParticipantIds:visit.fde_participant_ids||(visit.fde_participants||[]).map(p=>p.id),
    fdeParticipantNames:(visit.fde_participants||[]).map(p=>p.name).join("、"),
    signal: visitSignal(visit),
    date: visitDates.dateLabel(visit.visit_date || visit.interaction_at) || "日期未补充",
    createdAt: visitDates.dateLabel(visit.created_date || visit.created_at) || "未记录",
    recordedAt: businessTimeLabel(visit.created_at) || "未记录",
    creator: visit.creator_name || "未记录",
    recorderId: visit.recorder_id || "",
    sevenDaysText: visitDates.sevenLabel(visit.within_seven_days),
    customerName: visitCustomerId ? text(visit.customer_name || (String(visitCustomerId) === String(raw.id) ? raw.name : '') || '') : '',
    opportunityId: visit.opportunity_id || "",
    customerType: visitDates.typeLabel(visit.customer_type),
    opportunityName: linkedOpportunities.length ? linkedOpportunities.map(link=>link.name).join('、') : text(visit.opportunity_name || "不关联商机"),
    partnerName: text(visit.partner_name || visit.partner_name_snapshot || "未填写"),
    visitGoal: text(visit.visit_goal || "未记录"),
    collaborators: (visit.collaborators || []).map(c => c.name || c.display_name).join("、") || "未选择",
    mode: INTERACTION_MODE_NAME[visit.interaction_mode_code] || visit.interaction_mode_code || "待确认",
    title: !visit.customer_id && !visit.partner_id && linkedOpportunities.length ? '关联商机跟进' :
      text(visit.opportunity_name || (visit.partner_id && !visit.customer_id ? '伙伴跟进记录' : "客户沟通记录")),
    conclusion: EXPECTATION_NAME[visit.expectation_code] || visit.expectation_code || "待确认",
    owner: originalRecorderName || visit.recorder_name || "待确认",
    contactNames: visit.contact_name_snapshot || "待确认",
    location: visit.visit_location || "待确认",
    duration: visit.duration_minutes ? `${visit.duration_minutes}分钟` : "待确认",
    expectation: EXPECTATION_NAME[visit.expectation_code] || visit.expectation_code || "待确认",
    followUpRecord: text(visit.follow_up_record || "暂无跟进记录"),
    nextAction: text(visit.next_action || "暂无下一步行动"),
    isFirstVisit,
    firstVisitText: isFirstVisit ? "是" : "否",
    customerMainBusiness: text(value("customer_main_business") || "未记录"),
    customerNeeds: text(value("customer_needs") || "未记录"),
    customerBudget: text(value("customer_budget") || "未记录"),
    contactRole: text(value("contact_role") || "未记录"),
  };});
  const tasks = (raw.tasks || []).map(normalizeCustomerTask);
  normalizedOpportunities.forEach(o=>{o.relatedVisits=visits.filter(v=>v.opportunityId===o.id || v.linkedOpportunities.some(link=>String(link.id)===String(o.id)));o.relatedTasks=tasks.filter(t=>t.opportunityId===o.id);});
  const completedTaskCount = summary ? Number(taskStatusCounts.completed || 0) : tasks.filter((item) => item.status === "completed").length;
  const statusRisk = summary ? summary.status_risk : (raw.risks || []).find(risk => risk.status !== "resolved");
  const openRisk = summary ? summary.open_risk : (raw.risks || []).find((risk) => !["resolved", "accepted"].includes(risk.status));
  const contacts = (raw.contacts || []).map((contact) => ({
    id: contact.id,
    name: contact.name,
    title: contact.title || "职位待补充",
    role: contactRoleText(contact),
    strength: contact.is_primary ? "首要联系人" : "联系人",
  }));
  const dimensions = !summaryPending && raw.profile && Array.isArray(raw.profile.dimensions) ? raw.profile.dimensions : [];
  const customerProfile = ["客户潜力", "关系深度", "商机成熟", "拜访活跃", "决策链", "风险健康"].map((label,index)=>{
    const dimension=dimensions[index];
    const hasValue=Boolean(dimension && typeof dimension.value==='number' && Number.isFinite(dimension.value));
    return {label:dimension?dimension.label:label,value:hasValue?dimension.value:'—',hasValue};
  });
  const customerRadar=buildCustomerRadar(customerProfile);
  const profileComplete=customerRadar.points.length===6;
  const attributes = raw.attributes || {};
  const quadrantMeta = QUADRANTS.find((item) => item.name === customer.quadrant) || {};
  const detail = {
    ...customer,
    industry: raw.industry_code || "待补充",
    customerType: CUSTOMER_TYPE_NAME[raw.customer_type_code] || raw.customer_type_code || "待补充",
    annualValue: summary ? (summary.open_amount == null ? "—" : amountText(summary.open_amount)) : (opportunities.some(item=>item.status==="open"&&(item.amount==null||Number(item.amount)<0)) ? "—" : amountText(opportunities.filter(item=>item.status==="open").reduce((sum,item)=>sum+Number(item.amount),0))),
    cooperationYears: (raw.cooperation_years ?? attributes.cooperation_years) !== null && (raw.cooperation_years ?? attributes.cooperation_years) !== undefined ? `${raw.cooperation_years ?? attributes.cooperation_years}年` : "未登记",
    cooperationPending: (raw.cooperation_years ?? attributes.cooperation_years) === null || (raw.cooperation_years ?? attributes.cooperation_years) === undefined,
    potentialBasis: customer.potential === null ? '潜力待评估' : `数据库潜力评分 ${customer.potential}`,
    historicalOrders: "订单系统尚未接入",
    dailyBehavior: summary ? `共 ${summary.visit_count} 条拜访记录` : (visits.length ? `当前载入 ${visits.length} 条拜访记录` : "暂无已归档拜访"),
    currentOpportunitySummary: `${primaryOpportunity.name || "暂无活跃商机"} · ${primaryOpportunity.amount == null ? "—" : amountText(primaryOpportunity.amount)}`,
    grossProfitContract: "合同及毛利数据尚未接入",
    territoryId: `${customer.team} · 当前负责人 ${customer.owner}`,
    channelEcosystem: `${raw.source_code || "来源待补充"} · 合作伙伴：${raw.primary_partner_name || "无"}`,
    opportunity: {
      id: primaryOpportunity.id || "",
      name: primaryOpportunity.name || "暂无活跃商机",
      stage: opportunityStageText(primaryOpportunity.stage_code),
      amount: primaryOpportunity.amount == null ? "—" : amountText(primaryOpportunity.amount),
      probability: primaryProbability,
      expectedDate: expectedCloseText(primaryOpportunity),
    },
    signal:statusLight(statusRisk ? 'yellow' : 'green',statusRisk ? statusRisk.title : '当前未发现未解除风险'),
    opportunities: normalizedOpportunities,
    opportunityCount: summary ? summary.opportunity_count : normalizedOpportunities.length,
    contactCount: summary ? summary.contact_count : contacts.length,
    visitCount: summary ? summary.visit_count : visits.length,
    risk: openRisk ? openRisk.title : "暂无重大风险",
    riskDetail: openRisk ? (openRisk.description || openRisk.title) : "数据库当前没有未解除风险。",
    nextAction: (summary ? displayText(summary.latest_visit && summary.latest_visit.next_action, workspaceId) : visits[0] && visits[0].nextAction) || displayText(attributes.next_action, workspaceId) || "暂无下一步行动",
    potentialEvidence: [`潜力评分：${customer.potentialText}`],
    relationshipEvidence: [`关系评分：${customer.relationshipText}`, `已识别 ${summary ? summary.contact_count : contacts.length} 位联系人`],
    contacts,
    visits,
    tasks,
    taskCount: summary ? summary.task_count : tasks.length,
    completedTaskCount,
    pendingTaskCount: summary ? ["pending_confirm","pending_execution","in_progress","deferred","pending_review"].reduce((n,key)=>n+Number(taskStatusCounts[key]||0),0) : tasks.filter(item => ["pending_confirm", "pending_execution", "in_progress", "deferred", "pending_review"].includes(item.status)).length,
    quadrantDefinition: quadrantMeta.definition || "待计算",
    quadrantAction: quadrantMeta.action || "等待补充数据",
    updatedAt: summary ? (summary.latest_visit ? `更新于 ${dateText(summary.latest_visit.interaction_at || summary.latest_visit.visit_date)}` : "暂无拜访更新") : (visits.length ? `更新于 ${visits[0].date}` : "暂无拜访更新"),
    customerProfile,
    customerRadar,
    profileComplete,
    profileHint: profileComplete ? "基于当前可见的业务记录" : `已获取 ${customerRadar.points.length}/6 维数据，缺项待补充`,
  };
  if(summaryPending)Object.assign(detail,{
    summaryPending:true,annualValue:'—',opportunityCount:'—',contactCount:'—',visitCount:'—',
    taskCount:'—',completedTaskCount:'—',pendingTaskCount:'—',dailyBehavior:'拜访汇总待加载',
    signal:statusLight('gray','经营风险汇总待加载'),risk:'风险待评估',riskDetail:'经营汇总尚未加载。',
    nextAction:displayText(attributes.next_action||raw.next_action||'下一步行动待加载',workspaceId),updatedAt:raw.updated_at?`更新于 ${dateText(raw.updated_at)}`:'更新时间待加载',
    relationshipEvidence:[`关系评分：${customer.relationshipText}`,'联系人总数待加载'],
    profileComplete:false,profileHint:'经营画像待加载',
  });
  detail.relationshipLevel = customer.relationship === null ? '待评估' : Math.round(customer.relationship / 10);
  detail.mapDetailRows = [
    { label: "客户名称", value: detail.name },
    { label: "行业", value: detail.industry },
    { label: "关系等级", value: customer.relationship === null ? '待评估' : `${detail.relationshipLevel} / 10`, note: "数据库关系评分", emphasis: true },
    { label: "潜力", value: detail.potentialBasis, note: `潜力分 ${detail.potentialText}`, emphasis: true },
    { label: "历史订单", value: detail.historicalOrders },
    { label: "日常行为", value: detail.dailyBehavior },
    { label: "当前商机", value: detail.currentOpportunitySummary },
    { label: "毛利 / 合同", value: detail.grossProfitContract },
    { label: "负责人", value: detail.territoryId },
    { label: "渠道生态", value: detail.channelEcosystem },
  ];
  return detail;
}

module.exports = { amountText, normalizeCustomerSummary, normalizeCustomerDetail, normalizeCustomerTask, opportunityProbability, opportunityStageText, sortVisitsByTime };
