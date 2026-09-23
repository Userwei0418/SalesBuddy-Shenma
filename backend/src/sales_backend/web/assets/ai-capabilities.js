// Shared business names for provider calls and end-to-end Agent audit records.
export const capabilityLabels = Object.freeze({
  connectivity_test: "接口连通性测试",
  battle_map_review: "客户作战地图评估",
  visit_entry: "拜访记录结构化",
  visit_quality: "拜访记录质检",
  opportunity_draft: "商机新建/更新判断",
  personal_risks: "个人客户风险识别",
  today_tasks: "今日待办规划",
  operating_report: "经营即时总结",
  chatbi: "经营问数（二期）",
  customer_chatbi: "客户问数（二期）",
  customer_create: "客户建档",
  management_task: "管理任务草稿",
  competency_review: "销售六维能力复盘",
  customer_advice: "客户经营建议",
  opportunity_advice: "商机经营建议",
  opportunity_change: "商机变化评估",
  visit_advice: "单次拜访建议",
});

export function capabilityLabel(code) {
  const key = String(code ?? "").trim();
  if (!key) return "未记录业务能力";
  return Object.hasOwn(capabilityLabels, key)
    ? capabilityLabels[key]
    : "未识别的业务";
}
