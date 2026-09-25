const { activeWorkspaceId, displayText } = require('./demoDisplay');

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  // Business timestamps are displayed in China time regardless of device timezone.
  const local = new Date(date.getTime() + 8 * 3600000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${local.getUTCMonth() + 1}月${local.getUTCDate()}日 ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
}
function buildVisitReceipt(record) {
  const workspaceId = activeWorkspaceId();
  return {
    id: `visit_archived_${record.id}`,
    from: "agent",
    kind: "data-card",
    time: dateLabel(record.archived_at),
    sortAt: new Date(record.archived_at || 0).getTime() || 0,
    card: {
      tone: "green",
      eyebrow: "VISIT ARCHIVED",
      title: "拜访记录录入成功",
      subtitle: [displayText(record.customer_name, workspaceId), require('./visitDates').dateLabel(record.interaction_at), record.interaction_mode].filter(Boolean).join(" · "),
      metrics: [
        { value: `${record.completed_count}/${record.total_count}`, label: "字段已归档" },
        { value: record.score == null ? "—" : `${record.score}分`, label: "AI 评分" },
        { value: record.grade, label: "质量等级" },
      ],
      rows: [
        ...(record.opportunity_name ? [{ title: "关联商机", meta: displayText(record.opportunity_name, workspaceId), tag: "已关联" }] : []),
        { title: "下一步行动", meta: displayText(record.next_action, workspaceId), tag: "待执行" },
      ],
      action: { label: "查看客户档案", code: "open_archived_customer", customerId: record.customer_id },
    },
  };
}
function mergeVisitReceipts(messages, records) {
  const rest = messages.filter((m) => !String(m.id).startsWith("visit_archived_"));
  const seen = new Set();
  const receipts = [...records].sort((a, b) => new Date(b.archived_at || 0).getTime() - new Date(a.archived_at || 0).getTime()).filter((r) => {
    if (!r.id || seen.has(r.id)) return false;
    seen.add(r.id);
    return true;
  }).map(buildVisitReceipt);
  return [...rest.filter((m) => m.kind === "greeting"), ...receipts, ...rest.filter((m) => m.kind !== "greeting")];
}
module.exports = { buildVisitReceipt, mergeVisitReceipts };
