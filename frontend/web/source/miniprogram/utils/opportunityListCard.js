const { opportunitySignal } = require('./customerSignals');
const { amountText, opportunityProbability } = require('./customerDetail');
const { cardFields } = require('./opportunityCard');
const { stageOf, gradeOfAmount } = require('./opportunity');
const { expectedCloseQuarter } = require('./opportunityQuarter');

function dateText(value) {
  if (!value) return "暂无数据";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}
function fullDateText(value) {
  if (!value) return "待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function decorateOpportunity(item) {
  const stage = stageOf(item);
  const probability = opportunityProbability(item);
  const grade = gradeOfAmount(item.amount);
  const quarter = !item.expected_close_date && expectedCloseQuarter(item);
  const quarterText = quarter ? `${quarter.year} Q${quarter.quarter}` : '';
  return {
    ...item,
    ...cardFields(item),
    signal: opportunitySignal({
      ...item,
      probability
    }),
    probability,
    progressPercent: probability === null ? 0 : probability,
    probabilityText: probability === null ? "已关闭" : `${probability}%`,
    owner: item.owner_name || "待分配",
    team: item.team_name || "待分配团队",
    amountText: amountText(item.amount),
    amountLabel: amountText(item.amount),
    closeText: quarterText || dateText(item.expected_close_date),
    closeLabel: quarterText || fullDateText(item.expected_close_date),
    stageCode: stage.code,
    stageName: stage.label,
    stageLabel: stage.text,
    amountBandCode: grade ? grade.code : "",
    gradeCode: grade ? grade.code : "",
    gradeLabel: grade ? `${grade.code}级` : ""
  };
}

module.exports = { decorateOpportunity };
