function number(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function money(value) {
  const amount = number(value);
  return amount === null ? "未登记" : `¥${amount.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function targetAmount(maturity, kind) {
  const targets = maturity.targets || maturity.sales_targets || {};
  const candidates = [maturity[`${kind}_target_amount`], maturity[`${kind}_target`], targets[`${kind}_amount`], targets[kind]];
  return candidates.map(number).find((value) => value !== null) ?? null;
}

function performanceMetric(maturity, kind, actual) {
  const target = targetAmount(maturity || {}, kind);
  const completed = number(actual);
  const rate = target && completed !== null ? completed / target * 100 : null;
  return {
    kind,
    name: kind === "collection" ? "回款" : "确收",
    targetText: target === null ? "未设置" : money(target),
    completedText: completed === null ? "未登记" : money(completed),
    rateText: rate === null ? (target === null ? "目标待设置" : "实绩待登记") : `完成率 ${rate.toFixed(1)}%`,
    progress: rate === null ? 0 : Math.max(0, Math.min(100, Math.round(rate))),
  };
}

function customerRetention(rows, now = new Date()) {
  const year = now.getFullYear();
  const lastYear = year - 1;
  const byCustomer = new Map();
  (rows || []).forEach((row) => {
    const customerId = String(row.customer_id || "");
    const occurred = String(row.occurred_on || row.occurredAt || "");
    const rowYear = Number(occurred.slice(0, 4));
    const amount = number(row.recognized_amount !== undefined ? row.recognized_amount : row.amount);
    if (!customerId || ![year, lastYear].includes(rowYear) || amount === null) return;
    if (!byCustomer.has(customerId)) byCustomer.set(customerId, { current: 0, previous: 0 });
    byCustomer.get(customerId)[rowYear === year ? "current" : "previous"] += amount;
  });
  const cohort = [...byCustomer.values()].filter((item) => item.previous > 0);
  const previous = cohort.reduce((sum, item) => sum + item.previous, 0);
  const current = cohort.reduce((sum, item) => sum + item.current, 0);
  return {
    rate: previous > 0 ? current / previous * 100 : null,
    current,
    previous,
    customerCount: cohort.length,
  };
}

function averageVisitScore(visits) {
  const scores = (visits || []).map((item) => number(item.score !== undefined ? item.score : item.follow_up_score)).filter((value) => value !== null);
  return scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
}

function averageCustomerScore(customers) {
  const scores = (customers || []).map((item) => {
    const potential = number(item.potential_score !== undefined ? item.potential_score : item.potential);
    const relationship = number(item.relationship_score !== undefined ? item.relationship_score : item.relationship);
    return potential === null || relationship === null ? null : (potential + relationship) / 2;
  }).filter((value) => value !== null);
  return scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
}

function opportunityABShare(opportunities, gradeOfAmount) {
  const rows = opportunities || [];
  if (!rows.length) return null;
  const count = rows.filter((item) => {
    const grade = gradeOfAmount(item.amount);
    return grade && ["A", "B"].includes(grade.code);
  }).length;
  return count / rows.length * 100;
}

module.exports = { averageCustomerScore, averageVisitScore, customerRetention, money, opportunityABShare, performanceMetric };
