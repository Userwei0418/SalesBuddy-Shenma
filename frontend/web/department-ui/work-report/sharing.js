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
