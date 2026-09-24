// Current product display names follow the recorded Agent ID, never the business purpose of a call.
// Remote publication titles and historical receipts remain separate evidence.
const names = Object.freeze({
  "01a09022-c5ff-7562-bd7d-f8d6c74f5486": "Raccoon SalesBuddy·销售经营问数",
  "01a09021-b848-7e7f-9567-e20859d29bf8": "Raccoon SalesBuddy·客户作战地图评估",
  "01a09022-bb1a-7dab-8481-917e0472c528": "Raccoon SalesBuddy·个人客户风险识别",
  "01a09022-b58f-7214-85eb-29d035ea6f2c": "Raccoon SalesBuddy·商机新建更新判断",
  "01a09684-1968-77da-bfd0-e5e0ab59f015": "Raccoon SalesBuddy·商机经营建议（第一版）",
  "01a09fca-4a3b-7da4-8522-e68ca2b78074": "Raccoon SalesBuddy·商机经营建议（第二版）",
  "01a09eff-d317-7725-8941-7b8e37f4a641": "Raccoon SalesBuddy·拜访记录质检",
  "01a09022-c06d-7ac3-b863-8a1345b069ae": "Raccoon SalesBuddy·拜访记录结构化",
  "01a09a6c-97c7-7631-b580-c3b2b14739ad": "Raccoon SalesBuddy·销售六维能力复盘",
  "01a09022-d0ec-7703-b59f-0699150d63a7": "Raccoon SalesBuddy·销售经营即时总结",
  "01a09022-cb7b-7f33-8b58-410850ac1d8a": "Raccoon SalesBuddy·今日待办规划",
  "01a09684-1692-78ec-b1e7-07838158bb36": "Raccoon SalesBuddy·客户经营建议",
  "01a09684-1c0b-763f-b70c-84ea7d2dac08": "Raccoon SalesBuddy·单次拜访建议",
});
export const providers = Object.freeze({agent_platform:"中台智能体", senseaudio:"原模型接口", direct_api:"后台配置接口", rules:"原规则兜底"});
export const label = (map, key, fallback = "历史未记录") => Object.hasOwn(map, key) ? map[key] : fallback;
export const agentId = model => String(model || "").startsWith("agent:") ? String(model).slice(6) : null;
export const agentName = id => id ? label(names, id, "未登记名称的中台智能体") : "未记录智能体";
export const modelName = model => agentId(model) ? agentName(agentId(model)) : model ? "原模型接口" : "未记录模型";
export function calledAgents(operation) {
  const ids = [...new Set((operation.attempts || []).filter(a =>
    a.provider === "agent_platform" && !a.network_dispatch_suppressed).map(a => agentId(a.model)).filter(Boolean))];
  return ids.length ? ids.map(agentName).join("、") : "未记录中台请求";
}
const errors = Object.freeze({
  "competency_review.evidence_source":"能力复盘引用了未提供或重复的拜访记录",
  "competency_review.dimension_count":"能力复盘维度数量与公司规则不符",
  "competency_review.dimension_code":"能力复盘维度未定义或重复",
  "competency_review.score":"能力复盘分数不是零到一百分之间的有效数值",
  "competency_review.evidence":"能力复盘证据列表格式或数量不符合要求",
  TimeoutError:"中台响应超时", InvalidAgentResult:"中台返回未通过业务字段校验",
  ProviderUnavailable:"中台服务不可用", ControlledPlatformBlock:"测试拦截，未向中台发送",
  PlatformNotSelected:"中台未启用或未配置", fde_invalid_json:"返回内容无法解析为完整、无歧义的结构化对象",
  fde_expected_json_object:"返回类型错误，需要一个结构化对象",
  "today_tasks.due_conflicts_with_source":"建议时间与原任务截止时间冲突",
  "today_tasks.due_offset_or_format":"建议时间格式或时区不符合要求",
  "today_tasks.due_without_source":"建议时间缺少来源依据",
  "today_tasks.source_deadline_needs_confirmation":"原任务截止时间尚未确认",
});
export const errorName = code => code ? label(errors, code, "返回字段、类型或引用不符合业务契约，技术明细可导出核对") : "未记录异常";
export const requestStates = Object.freeze({succeeded:"请求成功",accepted:"请求成功",failed:"请求失败",running:"请求中",cancelled:"请求已取消",suppressed:"未发送"});
export const stopStates = Object.freeze({not_requested:"无需停止请求",acknowledged:"中台已确认停止",requested:"已请求停止",failed:"停止请求失败",not_available:"未取得可停止的任务编号",unknown:"停止状态未核实"});
