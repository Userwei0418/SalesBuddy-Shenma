// Unsaved input is local; its key follows account identity rather than display name.
function draftScope(session = {}) {
  const owner=`${session.workspaceId || 'unscoped'}:${session.userId || session.account || 'unauthenticated'}`;
  return ['fde','fde_lead'].includes(session.role)?`${owner}:${session.role}:${session.permissionVersion||'unverified'}`:owner;
}
module.exports = {draftScope};
