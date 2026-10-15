const access = require('./access');

const CANCELLABLE_STATUSES = ['pending_confirm', 'pending_execution', 'in_progress', 'deferred'];

function canCancelTask(task, session) {
  return !!task && task.can_cancel === true && access.can(session, 'task.cancel') &&
    CANCELLABLE_STATUSES.includes(task.status);
}

// Include the live capability as well as permissionVersion: revocation must also
// invalidate a pending confirmation when the session object is updated in place.
function cancellationIdentity(session) {
  return `${access.identity(session)}:${access.can(session, 'task.cancel')}`;
}

module.exports = { canCancelTask, cancellationIdentity };
