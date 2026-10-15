// These names describe the recorded transfer, not the task's current owner.
// Older receipts may contain IDs only; never infer a historical name from them.
function taskTransferSummary(payload = {}) {
  const previous = Array.isArray(payload.previous_owners) ? payload.previous_owners : [];
  const names = previous.map(owner => owner && typeof owner.name === 'string' ? owner.name.trim() : '');
  const nextValue = payload.new_owner && (payload.new_owner.display_name || payload.new_owner.name);
  const next = typeof nextValue === 'string' ? nextValue.trim() : '';
  return names.length && names.every(Boolean) && next ? `${names.join('、')} → ${next}` : '';
}

function taskStageLabel(task) {
  if (task.handover_required) return '接收资格已变化，等待协调处理';
  if (task.status === 'completed') return '任务已完成，结果已同步';
  if (task.status === 'pending_confirm') return '等待接收方接受或拒绝';
  if (task.status === 'pending_review') return '完成说明已提交，等待发起人验收';
  if (task.status === 'cancelled') return task.last_event_type === 'cancel'
    ? '任务已取消，原因和协调记录已保留' : '接收方已拒绝，等待发起人决定是否重新发起';
  return '任务执行中 · 等待完成反馈';
}

module.exports = { taskTransferSummary, taskStageLabel };
