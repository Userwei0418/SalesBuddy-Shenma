const { identity } = require('./access');

// A page can outlive a login, a selected record, or a confirmation dialog.
// Keep the request's identity and target immutable; transport idempotency stays
// in apiClient/requestIdentity and is not reimplemented here.
function capturePageContext(getSession, page, resource = () => '') {
  const session = getSession();
  const owner = identity(session), target = JSON.stringify(resource());
  const current = () => !page.unloaded && !page.disposed &&
    owner === identity(getSession()) &&
    (page.writeIdentity === undefined || page.writeIdentity === owner) &&
    target === JSON.stringify(resource());
  return { session: { ...session }, identity: owner, current,
    visible: () => current() && !page.writeHidden && !page.hidden && !page.closed };
}

function beginPageWrite(getSession, page, slot, resource) {
  const context = capturePageContext(getSession, page, resource);
  if (!context.current()) return null;
  const operations = page.pageWrites || (page.pageWrites = {});
  if (operations[slot] && operations[slot].current()) return null;
  const operation = { ...context,
    settledVisible: context.visible,
    current: () => operations[slot] === operation && context.current(),
    visible: () => operations[slot] === operation && context.visible(),
    start() {
      if (operation.started || !operation.current()) return false;
      operation.started = true;
      return true;
    },
    finish(flag) {
      if (operations[slot] !== operation) return;
      delete operations[slot];
      if (flag && context.current()) page.setData({ [flag]: false });
    },
  };
  operations[slot] = operation;
  return operation;
}

function createPageWriteContext(getSession) {
  return { capture: (page, resource) => capturePageContext(getSession, page, resource),
    begin: (page, slot, resource) => beginPageWrite(getSession, page, slot, resource) };
}

module.exports = { createPageWriteContext };
