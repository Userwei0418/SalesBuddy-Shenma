export function sharingAudience(value,targets) {
  if(!targets?.allowed_kinds.includes(value?.kind))throw Error('请选择当前允许的共享范围');
  if(value.kind==='members') {
    const members=[...new Set(value.member_ids || [])];
    if(!members.length || members.length>100 || members.some(id=>!targets.members.some(item=>item.id===id)))throw Error('请选择可接收的成员');
    return {kind:'members',member_ids:members};
  }
  if(value.kind==='team') {
    if(!targets.teams.some(item=>item.id===value.team_id))throw Error('请选择可接收的部门');
    return {kind:'team',team_id:value.team_id};
  }
  return {kind:'company'};
}


export function checkedSharePreview(value,version) {
  const counts=['recipient_count','readable_count','unreadable_count'];
  if(!value || value.version_no!==version || counts.some(key=>!Number.isSafeInteger(value[key]) || value[key]<0) ||
    value.readable_count+value.unreadable_count!==value.recipient_count || typeof value.can_share!=='boolean' ||
    typeof value.confirmation_required!=='boolean' || !/^[a-f0-9]{64}$/.test(value.preview_hash || '') ||
    value.can_share && value.readable_count===0 || value.unreadable_count>0 && value.can_share && !value.confirmation_required)throw Error('共享权限核对结果不完整，请重新核对');
  return value;
}
export function shareConfirmation(preview,confirmedHash) {
  if(!preview?.can_share || !preview.readable_count)return null;
  if(preview.confirmation_required && confirmedHash!==preview.preview_hash)return null;
  // Even an all-readable selection is bound to the counts just displayed.
  return {visibility_confirmation:preview.preview_hash};
}
export function shareErrorText(error) {
  return {
    WORK_REPORT_SHARE_CONFIRMATION_REQUIRED:'接收范围或来源权限已变化，请重新核对人数并确认。',
    WORK_REPORT_SHARE_NO_RECIPIENT:'当前没有可查看此报告的接收人，请调整范围。',
    WORK_REPORT_SHARE_SOURCE_DENIED:'所选接收范围不能查看完整报告，请调整范围。',
    WORK_REPORT_SHARE_SCOPE_CHANGED:'共享对象已失效或超出当前共享权限，请重新选择。',
  }[error?.serverCode || error?.code] || error?.message || '共享未完成，请重试';
}

export const shareSelectionFailure=error=>['WORK_REPORT_SHARE_CONFIRMATION_REQUIRED','WORK_REPORT_SHARE_NO_RECIPIENT','WORK_REPORT_SHARE_SOURCE_DENIED','WORK_REPORT_SHARE_SCOPE_CHANGED'].includes(error?.serverCode || error?.code);

export function shareResultText(visibility) {
  try {
    const value=checkedSharePreview(visibility,visibility?.version_no);
    return `报告已共享。本次核对可查看 ${value.readable_count} 人，不可查看 ${value.unreadable_count} 人；后续查看仍按当时权限校验。`;
  } catch(_) {return '报告已共享。可查看人数暂未取得，可重新打开管理共享核对。';}
}
