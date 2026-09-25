const STATES={green:{label:'健康',color:'#238c68',background:'#e4f6ee'},yellow:{label:'提醒',color:'#a77722',background:'#fff4d9'},red:{label:'告警',color:'#bc5149',background:'#ffebe8'},gray:{label:'待评估',color:'#8091a0',background:'#eef2f5'}};
function statusLight(tone,reason='') {const state=STATES[tone]||STATES.gray;return {...state,tone:STATES[tone]?tone:'gray',reason};}
function scoreLight(value) {
 if(!['number','string'].includes(typeof value)||String(value).trim()===''||!Number.isFinite(Number(value)))return statusLight('gray','暂无有效评分');
 const score=Math.round(Number(value));
 return statusLight(score>=80?'green':score>=60?'yellow':'red',score>=80?'评分80分及以上':score>=60?'评分60至79分':'评分低于60分');
}
function riskLight(item={}) {return statusLight(item.status==='resolved'?'green':'yellow',item.status==='resolved'?'风险已解除':item.status==='accepted'?'风险已接受，仍需关注':item.title||'存在待处理风险');}
function taskLight(item={},now=Date.now()) {
 if(item.status==='completed')return statusLight('green','任务已完成');
 if(['cancelled','rejected'].includes(item.status))return statusLight('yellow','任务已拒绝或取消，需确认后续安排');
 const due=Date.parse(item.due_at||item.dueAt||'');
 if(Number.isFinite(due)&&due<Number(now))return statusLight('red','任务已逾期且未完成');
 if(item.status==='pending_review')return statusLight('yellow','完成说明已提交，等待发起人确认');
 if(['pending_confirm','pending_execution','in_progress','deferred','pending'].includes(item.status))return statusLight('yellow','任务尚未完成，请关注负责人和期限');
 return statusLight('gray','任务状态未明确');
}
function businessChangeLight(payload={}) {
 const review=payload.change_review;
 const labels={green:'向好',yellow:'需关注',red:'转差',gray:'已更新'};
 if(review && review.status==='completed' && STATES[review.color]) {
   return {...statusLight(review.color,review.summary||''),label:labels[review.color],title:review.title};
 }
 if(review && review.status==='pending') return {...statusLight('gray','正在评估本次变化'),title:'商机信息已更新'};
 if(payload.opportunity_id) {
   const tone={orange:'yellow',blue:'gray'}[payload.tone]||payload.tone;
   return {...statusLight(tone),label:labels[tone]||'待评估'};
 }
 const score=scoreLight((payload.ai_review||{}).relationship_after);
 // Explicit backend alerts take priority; blue only means an update, not health.
 if(payload.tone==='red')return statusLight('red','存在明确告警信号');
 if(score.tone==='red')return score;
 if(['yellow','orange'].includes(payload.tone))return statusLight('yellow','存在需要关注的提醒');
 if(score.tone!=='gray')return score;
 return payload.tone==='green'?statusLight('green','后台评估为健康'):statusLight('gray','暂无有效健康评估');
}
module.exports={STATES,statusLight,scoreLight,riskLight,taskLight,businessChangeLight};
