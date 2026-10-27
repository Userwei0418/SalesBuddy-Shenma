// Display only the server's known, frozen opportunity-state sentence. Ordinary
// prose (including user English) is not searched/replaced by individual words.
const STAGES={identified:'意向沟通（10%）',qualified:'商机确认（30%）',solution:'方案沟通（50%）',proposal:'商务谈判（70%）',negotiation:'客户签约（90%）',won:'赢单',lost:'丢单'};
const STATUSES={open:'推进中',won:'已赢单',lost:'已丢单'};
export function sourceDisplayText(text,refs=[]) {
  if(!refs.some(ref=>typeof ref==='string' && ref.startsWith('opportunity:')))return text;
  return text.replace(/快照时点的当前状态：([a-z_]+)；([a-z_]+)。不能据此认定本期阶段推进。/g,(original,stage,status)=>
    STAGES[stage] && STATUSES[status]?`快照时点的当前状态：${STAGES[stage]}；${STATUSES[status]}。不能据此认定本期阶段推进。`:original);
}
