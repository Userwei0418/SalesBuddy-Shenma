const firstVisit = require("./visitFirstVisit");
const opportunity = require("./opportunity");

const CORE = [
  {
    key: "follow_up_record",
    label: "沟通内容",
    placeholder: "实际沟通内容、客户反馈和达成结果",
  },
  {
    key: "next_action",
    label: "下一步计划",
    placeholder: "明确时间、负责人和具体行动",
  },
];
const OPTIONAL = [
  { key: "partner_name", label: "伙伴名称" },
  { key: "contact_name", label: "对接人" },
];
const REQUIRED_DETAILS = [
  { key: "interaction_at", label: "跟进日期" },
  { key: "created_date", label: "创建时间" },
  { key: "contact_name", label: "对接人" },
];
function reviewText(values) {
  const lines = [
    "拜访审核 v2",
    `拜访结果：${String(values.follow_up_record || "").trim()}`,
    `下一步行动计划：${String(values.next_action || "").trim()}`,
  ];
  if (firstVisit.enabled(values)) {
    lines.push(
      "拜访类型：首次拜访",
      ...firstVisit.REQUIRED_FIELDS.map(
        (field) => `${field.label}：${String(values[field.key] || "").trim()}`,
      ),
    );
  }
  return lines.join("\n");
}
function admissionPolicy(quality) {
  const p = quality && quality.admission_policy || {};
  return { score_threshold: Number.isInteger(p.score_threshold) ? p.score_threshold : 60,
    inclusive: p.inclusive === true, good_score: p.good_score || 80, excellent_score: p.excellent_score || 90 };
}
function scorePasses(quality) {
  if (!quality || !Number.isInteger(quality.follow_up_score) || quality.follow_up_score < 0 || quality.follow_up_score > 100) return false;
  const p = admissionPolicy(quality);
  return p.inclusive ? quality.follow_up_score >= p.score_threshold : quality.follow_up_score > p.score_threshold;
}
function admissionRequirement(quality) {
  const p = admissionPolicy(quality);
  return `${p.inclusive ? "不低于" : "高于"} ${p.score_threshold} 分`;
}
function grade(score, quality) {
  const p = admissionPolicy(quality);
  if (!scorePasses({ ...quality, follow_up_score: score })) return "待完善";
  return score >= p.excellent_score ? "优秀" : score >= p.good_score ? "良好" : "合格";
}
function canArchive(values, customerId, quality, stale, runId) {
  return Boolean(
    customerId &&
      runId &&
      !stale &&
      CORE.every((f) => String(values[f.key] || "").trim()) &&
      REQUIRED_DETAILS.every((f) => String(values[f.key] || "").trim()) &&
      !firstVisit.missingField(values) &&
      quality &&
      scorePasses(quality) &&
      quality.next_action &&
      quality.next_action.passed === true,
  );
}
function archiveBlockReason(d) {
  if (d.busy) return "正在处理，请稍候";
  if (!d.customerConfirmed || !d.customerId) return "请先确认本次拜访的关联客户";
  const missing = CORE.find((f) => !String(d.values[f.key] || "").trim());
  if (missing) return `请补充${missing.label}`;
  const missingDetail = REQUIRED_DETAILS.find((f) => !String(d.values[f.key] || "").trim());
  if (missingDetail) return `请补充${missingDetail.label}`;
  const missingFirstVisit = firstVisit.missingField(d.values);
  if (missingFirstVisit) return `首次拜访请补充${missingFirstVisit.label}`;
  if (!d.reviewRunId || !d.quality || !Number.isInteger(d.quality.follow_up_score) ||
      d.quality.follow_up_score < 0 || d.quality.follow_up_score > 100) return "请先完成 AI 质量审核";
  if (d.reviewStale) return "拜访正文已修改，请重新 AI 审核";
  if (!scorePasses(d.quality)) return `AI 质量审核须${admissionRequirement(d.quality)}，请修改后重新审核`;
  if (!d.quality.next_action || d.quality.next_action.passed !== true)
    return "下一步审核未通过，请补充具体日期和明确的行动计划或目标";
  return "";
}
function restoreReview(source, draft) {
  const restored=draft || {values:(source.result || {}).fields || {}};
  if(restored.flowVersion===1 && restored.reviewPayload && restored.reviewRunId)return restored;
  return {...restored,quality:null,reviewRunId:'',reviewedContent:'',reviewStale:true,flowStep:'edit'};
}
function suggestionText(value) { return value === null || value === undefined ? "" : String(value).trim(); }
function firstSuggestion(source, keys) {
  for (const key of keys) if (suggestionText(source[key])) return source[key];
  return "";
}
function suggestionAmountWan(source) {
  const direct = firstSuggestion(source, ["amount_wan", "acv_wan", "acv_amount_wan", "ACV（万元）", "ACV(万元)"]);
  if (suggestionText(direct)) return suggestionText(direct).replace(/[,，]/g, "").replace(/万(元)?$/, "");
  const raw = suggestionText(firstSuggestion(source, ["amount", "acv", "estimated_amount", "opportunity_amount", "ACV", "商机金额"])).replace(/[,，]/g, "");
  if (!raw) return "";
  const value = Number(raw.replace(/[万元￥¥]/g, ""));
  if (!Number.isFinite(value)) return "";
  if (/万/.test(raw)) return String(value);
  if (/元/.test(raw) || value >= 10000) return String(value / 10000);
  return String(value);
}
function suggestionStageIndex(source) {
  const probability = Number(firstSuggestion(source, ["probability", "win_probability", "stage_probability", "赢单概率"]));
  if (Number.isFinite(probability)) {
    const index = opportunity.STAGES.findIndex(item => item.probability === probability);
    if (index >= 0) return index;
  }
  const raw = suggestionText(firstSuggestion(source, ["stage_code", "stage", "opportunity_stage", "商机阶段"])).toLowerCase();
  if (!raw) return -1;
  return opportunity.STAGES.findIndex(item => raw === item.code || raw.includes(item.label.toLowerCase()) || raw.includes(String(item.probability)));
}
function normalizeOpportunitySuggestion(result = {}) {
  const source = result.opportunity_draft || result.opportunity || result.fields || result;
  const suggestion = {
    opportunityId: suggestionText(firstSuggestion(source, ["opportunity_id", "id"])),
    name: suggestionText(firstSuggestion(source, ["name", "opportunity_name", "project_name", "商机名称", "项目名称"])),
    amount: suggestionAmountWan(source),
    expected_close_date: suggestionText(firstSuggestion(source, ["expected_close_date", "close_date", "预计关单日期", "预计签约日期"])).slice(0, 10),
    stageIndex: suggestionStageIndex(source),
    partner_name: suggestionText(firstSuggestion(source, ["partner_name", "partner", "所属伙伴", "合作伙伴"])),
    product_line: suggestionText(firstSuggestion(source, ["product_line", "产品线"])),
  };
  suggestion.hasContent = Boolean(suggestion.opportunityId || suggestion.name || suggestion.amount || suggestion.expected_close_date || suggestion.stageIndex >= 0 || suggestion.partner_name || suggestion.product_line);
  return suggestion;
}
function matchExistingOpportunity(items, suggestion) {
  if (!suggestion || !suggestion.hasContent) return null;
  if (suggestion.opportunityId) {
    const byId = (items || []).find(item => String(item.id) === suggestion.opportunityId);
    if (byId) return byId;
  }
  const name = suggestionText(suggestion.name).toLowerCase();
  if (!name) return null;
  return (items || []).find(item => {
    const candidate = suggestionText(item.name).toLowerCase();
    return candidate === name || (candidate.length >= 6 && (candidate.includes(name) || name.includes(candidate)));
  }) || null;
}
function formFromSuggestion(existing, suggestion) {
  const form = opportunity.formFor(existing || null);
  if (!suggestion) return form;
  ["name", "amount", "expected_close_date", "partner_name", "product_line"].forEach(key => {
    if (suggestionText(suggestion[key])) form[key] = suggestionText(suggestion[key]);
  });
  if (suggestion.stageIndex >= 0) form.stageIndex = suggestion.stageIndex;
  return form;
}
module.exports = { admissionPolicy, scorePasses, admissionRequirement, CORE, OPTIONAL, REQUIRED_DETAILS, reviewText, grade, canArchive, archiveBlockReason, restoreReview,
  formFromSuggestion, matchExistingOpportunity, normalizeOpportunitySuggestion };
