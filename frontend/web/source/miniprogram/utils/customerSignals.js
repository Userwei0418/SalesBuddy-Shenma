const { beijingDateParts, expectedCloseQuarter } = require('./opportunityQuarter');
const { statusLight } = require('./statusLight');
const { STAGES } = require('./opportunity');
const signal=(tone,detail,reason)=>({...statusLight(tone,reason),detail});
const dateOnly=value=>/^\d{4}-\d{2}-\d{2}/.test(String(value||''))?String(value).slice(0,10):'';
function opportunitySignal(item,risks=[],now=new Date()) {
  if(item.status==='won')return signal('green','已成单','商机已确认赢单');
  if(item.status==='lost')return signal('red','已丢单','商机已确认丢单，建议复盘原因');
  const stage=STAGES.find(s=>s.status==='open'&&s.probability===Number(item.probability))
    || STAGES.find(s=>s.status==='open'&&s.code===item.stage_code);
  const stageLabel=stage?stage.label:'阶段待确认';
  const linked=risks.filter(r=>r.opportunity_id&&String(r.opportunity_id)===String(item.id)&&r.status!=='resolved');
  const high=item.risk_summary ? item.risk_summary.high_risk : linked.find(r=>['critical','high'].includes(r.severity_code));
  const first=item.risk_summary ? item.risk_summary.first_risk : linked[0];
  const today=new Date(new Date(now).getTime()+8*3600000).toISOString().slice(0,10);
  const expected=beijingDateParts(item.expected_close_date)?dateOnly(item.expected_close_date):'';
  if(expected&&expected<today)return signal('red','关单计划逾期',`预计关单日 ${expected} 已过，当前为${stageLabel}，尚未确认赢单；请核实推进情况并更新关单计划`);
  if(high)return signal('yellow','高风险',high.title||'存在未解除的高风险');
  if(first)return signal('yellow','需关注',first.title||'存在待处理风险');
  if(!expected) {
    const quarter=expectedCloseQuarter(item);
    if(quarter)return signal('gray','季度计划',`预计关单为 ${quarter.year} Q${quarter.quarter}，未记录具体日期，暂不判断按日逾期`);
    return signal('yellow','待补充','尚未填写预计关单日期');
  }
  const days=(Date.parse(expected+'T00:00:00+08:00')-Date.parse(today+'T00:00:00+08:00'))/86400000;
  if(days<=7) {
    const guidance={
      identified:['商机待确认','先确认商机是否成立，再核实关单计划'],
      qualified:['方案待推进','推进方案沟通，并核实关单计划'],
      solution:['方案待落实','落实方案并推进商务谈判，核实关单计划'],
      proposal:['谈判待推进','明确商务条款和下一步安排，核实关单计划'],
      negotiation:['签约待落实','核实合同签署与赢单确认情况'],
    };
    const [detail,action]=stage?guidance[stage.code]:['阶段待确认','补充当前商机阶段并核实关单计划'];
    return signal('yellow',detail,`预计关单日 ${expected} 在7天内，当前为${stageLabel}；请${action}`);
  }
  if(!['open','won','lost'].includes(item.status))return signal('gray','待评估','尚无明确的商机状态');
  if(!stage)return signal('gray','阶段待确认','尚无明确的商机阶段，请补充后评估推进情况');
  return signal('green','正常推进',`当前为${stageLabel}，预计关单未逾期`);
}
function visitSignal(visit = {}) {
  const review = visit.quality_review && typeof visit.quality_review === 'object'
    ? visit.quality_review : {};
  const validScore = value => Number.isInteger(value) && value >= 0 && value <= 100;
  const result = (tone, label, score, reason) => {
    const detail = score === null ? '' : `${score} 分`;
    return {...statusLight(tone, reason), label, detail, badgeText: detail ? `${label} · ${detail}` : label};
  };
  if (validScore(review.follow_up_score) && validScore(visit.follow_up_score)
      && review.follow_up_score !== visit.follow_up_score) {
    return result('gray', '质检结果待核对', null, '保存的质检分数不一致，请核对完整记录');
  }
  const score = validScore(review.follow_up_score) ? review.follow_up_score
    : validScore(visit.follow_up_score) ? visit.follow_up_score : null;
  if (score === null) return result('gray', '暂无质检结果', null, '此记录暂无可用的质检结果');
  // Use the saved grade: company thresholds can change after this review.
  // Record quality is independent of the legacy business-goal expectation_code.
  const tones = {优秀:'green', 良好:'green', 合格:'green', 待完善:'yellow'};
  const grade = typeof review.grade === 'string' ? review.grade.trim() : '';
  if (!Object.prototype.hasOwnProperty.call(tones, grade)) {
    return result('gray', '质检评分', score, '已有质检分数，尚无可用的质检等级');
  }
  const nextAction = review.next_action_passed === true ? '；下一步计划审核通过'
    : review.next_action_passed === false ? '；下一步计划仍需完善' : '';
  return result(tones[grade], `质检${grade}`, score, `沟通记录质检${grade}${nextAction}`);
}
module.exports={opportunitySignal,visitSignal};
