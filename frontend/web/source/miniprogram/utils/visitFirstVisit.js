const { normalizeVisitFieldValue } = require("./visitFieldNormalizer");

const REQUIRED_FIELDS = [
  {
    key: "customer_main_business",
    label: "客户主营业务",
    placeholder: "填写客户主要产品、服务或业务范围",
    aliases: ["customer_business", "main_business", "industry"],
  },
  {
    key: "customer_needs",
    label: "客户需求",
    placeholder: "填写客户当前需求和希望解决的问题",
    aliases: ["customer_need", "customer_demand", "demand_summary"],
  },
  {
    key: "customer_budget",
    label: "客户预算",
    placeholder: "填写已确认的预算金额或预算范围",
    aliases: ["budget", "estimated_budget"],
  },
  {
    key: "contact_role",
    label: "联系人角色",
    placeholder: "请选择联系人角色",
    aliases: ["relationship_role", "contact_relationship_role"],
  },
];

const CONTACT_ROLE_OPTIONS = require('./businessOptions').customer.contact_role;

function hasText(value) {
  return Boolean(String(value == null ? "" : value).trim());
}

function enabled(values) {
  const value = values && values.is_first_visit;
  return value === true || value === 1 || value === "1" || value === "true";
}

function normalizeValues(values, isFirstVisit) {
  const source = values || {};
  const next = { ...source, is_first_visit: Boolean(isFirstVisit) };
  if (!isFirstVisit) return next;
  REQUIRED_FIELDS.forEach((field) => {
    const raw = [field.key, ...field.aliases]
      .map((key) => source[key])
      .find(hasText);
    next[field.key] = normalizeVisitFieldValue(field.key, raw || "");
  });
  return next;
}

function missingField(values) {
  if (!enabled(values)) return null;
  return REQUIRED_FIELDS.find((field) => !hasText(values[field.key])) || null;
}

function buildAgentText(original, isFirstVisit) {
  const text = String(original || "").trim();
  if (!isFirstVisit) return text;
  return [
    "【录入类型：首次拜访】",
    "请在常规拜访字段之外，识别客户主营业务、客户需求、客户预算、联系人角色；原文未明确的信息请留空，交由销售补充，不要猜测。",
    "",
    "【拜访原始记录】",
    text,
  ].join("\n");
}

module.exports = {
  CONTACT_ROLE_OPTIONS,
  REQUIRED_FIELDS,
  buildAgentText,
  enabled,
  missingField,
  normalizeValues,
};
