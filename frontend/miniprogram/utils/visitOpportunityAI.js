const opportunity = require("./opportunity");

function text(value) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function first(source, keys) {
  for (const key of keys) {
    if (text(source[key])) return source[key];
  }
  return "";
}

function amountWan(source) {
  const direct = first(source, ["amount_wan", "acv_wan", "acv_amount_wan", "ACV（万元）", "ACV(万元)"]);
  if (text(direct)) return text(direct).replace(/[,，]/g, "").replace(/万(元)?$/, "");
  const raw = text(first(source, ["amount", "acv", "estimated_amount", "opportunity_amount", "ACV", "商机金额"])).replace(/[,，]/g, "");
  if (!raw) return "";
  const number = Number(raw.replace(/[万元￥¥]/g, ""));
  if (!Number.isFinite(number)) return "";
  if (/万/.test(raw)) return String(number);
  return String(number / 10000); // API canonical amount is yuan, regardless of magnitude.
}

function stageIndex(source) {
  if (source.status === "lost") return opportunity.STAGES.findIndex(item => item.status === "lost");
  if (source.status === "won") return opportunity.STAGES.findIndex(item => item.status === "won");
  const probability = Number(first(source, ["probability", "win_probability", "stage_probability", "赢单概率"]));
  if (Number.isFinite(probability)) {
    const index = opportunity.STAGES.findIndex((item) => item.probability === probability);
    if (index >= 0) return index;
  }
  const raw = text(first(source, ["stage_code", "stage", "opportunity_stage", "商机阶段"])).toLowerCase();
  if (!raw) return -1;
  return opportunity.STAGES.findIndex((item) => raw === item.code || raw.includes(item.label.toLowerCase()) || raw.includes(String(item.probability)));
}

function normalizeOpportunitySuggestion(result = {}) {
  const source = result.opportunity_draft || result.opportunity || result.fields || result;
  if (source.action === "none") return { hasContent: false, stageIndex: -1 };
  const suggestion = {
    opportunityId: text(first(source, ["opportunity_id", "id"])),
    name: text(first(source, ["name", "opportunity_name", "project_name", "商机名称", "项目名称"])),
    amount: amountWan(source),
    expected_close_date: text(first(source, ["expected_close_date", "close_date", "预计关单日期", "预计签约日期"])).slice(0, 10),
    stageIndex: stageIndex(source),
    partner_name: text(first(source, ["partner_name", "partner", "所属伙伴", "合作伙伴"])),
    product_line: text(first(source, ["product_line", "产品线"])),
  };
  suggestion.hasContent = Boolean(suggestion.opportunityId || suggestion.name || suggestion.amount || suggestion.expected_close_date || suggestion.stageIndex >= 0 || suggestion.partner_name || suggestion.product_line);
  return suggestion;
}

function matchExistingOpportunity(items, suggestion) {
  if (!suggestion || !suggestion.hasContent) return null;
  if (suggestion.opportunityId) {
    const byId = (items || []).find((item) => String(item.id) === suggestion.opportunityId);
    if (byId) return byId;
  }
  const name = text(suggestion.name).toLowerCase();
  if (!name) return null;
  return (items || []).find((item) => {
    const candidate = text(item.name).toLowerCase();
    return candidate === name || (candidate.length >= 6 && (candidate.includes(name) || name.includes(candidate)));
  }) || null;
}

function formFromSuggestion(existing, suggestion) {
  const form = opportunity.formFor(existing || null);
  if (!suggestion) return form;
  for (const key of ["name", "amount", "expected_close_date", "partner_name", "product_line"]) {
    if (text(suggestion[key])) form[key] = text(suggestion[key]);
  }
  if (suggestion.stageIndex >= 0) form.stageIndex = suggestion.stageIndex;
  return form;
}

module.exports = { formFromSuggestion, matchExistingOpportunity, normalizeOpportunitySuggestion };
