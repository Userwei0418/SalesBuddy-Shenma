const note=value=>typeof value==='string'?value.trim():'';
const PROCESSING_LABELS={adjustment_request:'调整请求',paused:'已暂停',awaiting_review:'待验收'};

// Display the authorized /review/activities projection; do not infer an
// outcome from status or reconstruct feedback from unrelated task records.
export const taskProcessingLabel=row=>PROCESSING_LABELS[row.processing_reason] || '';
export function taskDetailFields(row,{completed=false}={}) {
  if(completed)return [[row.completion_action==='close'?'停止说明':'完成结果',note(row.completion_result) || note(row.completion_note) || '暂无记录']];
  const fields=[['最新反馈',note(row.latest_feedback) || '暂无记录']];
  if(note(row.next_step))fields.push(['下一步',note(row.next_step)]);
  if(note(row.processing_detail))fields.push(['处理说明',note(row.processing_detail)]);
  return fields;
}
