// This is a presentation of frozen server facts, not a second weekly calculation.
export const SALES_WEEK_FACTS = 'work_report.sales_week_facts.v1';
export const WEEK_SECTION_LABELS = {summary:'本周工作总结',actual_collection:'本周实际回款',next_week_plan:'下周工作计划',next_week_collection:'下周预计回款',ppl_first_30:'本周新增 ≥30% PPL',ppl_open_50:'全部在推 ≥50% PPL'};
const states = {available:'材料已取得',partial:'部分材料',unknown:'暂无法核定',not_selected:'未选择对应来源'};
const count = value => Number.isInteger(value) && value >= 0 ? String(value) : '未知';
const instant = value => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short',hour12:false}).format(new Date(value)) : '未记录';
export function salesWeekView(facts, sections) {
  if (!facts) return null;
  if (facts.facts_contract !== SALES_WEEK_FACTS) return {supported:false,message:'本次周报材料版本暂不受支持，请更新后重新准备材料。'};
  const enabled = new Map(sections.filter(row=>row.enabled).map(row=>[row.id,row.title]));
  return {supported:true,asOf:`当前商机状态取数于 ${instant(facts.data_as_of)}，不代表所选历史周末的状态。`,
    nextPeriod:`下周计划范围：${instant(facts.next_period?.start)} 至 ${instant(facts.next_period?.end_exclusive)}（不含，北京时间）。`,
    sections:(facts.sections || []).filter(row=>enabled.has(row.section_id)).map(row=>({
      id:row.section_id,title:enabled.get(row.section_id),state:states[row.availability] || '状态未知',
      complete:row.availability==='available' && row.population_complete===true && row.content_complete===true,
      coverage:`已纳入 ${count(row.included)} / ${count(row.population_total)} 项`,
      notes:[...(row.missing_reasons || []),...(row.population_complete!==true?['当前授权范围的完整集合尚未核定。']:[]),
        ...(row.content_complete!==true?['正文材料尚未完整取得。']:[])],
    })),
    precisionNote:facts.finance?.precision_warning==='not_allocated_to_days'
      ? `有 ${count(facts.finance.unallocated_period_count)} 个期间的回款仅登记到季度，无法分配至本周，未计入本周回款。` : '',
  };
}

export function actualDate(lineage) {
  if (!lineage) return null;
  if (lineage.period_precision==='quarter') return `${lineage.year} 年第 ${lineage.quarter} 季度 · 具体回款日未登记，不能计入周回款`;
  if (lineage.period_precision==='day' && /^\d{4}-\d{2}-\d{2}$/.test(lineage.occurred_on || '')) return `回款日期 ${lineage.occurred_on}（按日登记）`;
  return '回款日期未登记，不能核定所属周';
}
