const access = require('./access');
const {adviceResult} = require('./customerAdvice');

// Reading advice is independent of editing the underlying business record.
// The API still checks the current actor and the requested business object.
function adviceAllowed(session, kind, generate = false) {
  if (!session) return false;
  if (!session.permissions) return kind !== 'customer' || !access.isFde(session.role);
  return access.can(session, 'advice.read') && access.can(session, `advice.${kind}`) &&
    (!generate || access.can(session, 'advice.request'));
}

function adviceState(value, kind, id, section, context = {}) {
  if (value === null && context.allowEmpty === true) return {status:'idle', summary:'', rows:[]};
  const mismatch = !value || !value.id || value.subject_kind !== kind ||
    String(value.subject_id) !== String(id) || value.section !== section ||
    (context.analysisId && String(value.id) !== String(context.analysisId)) ||
    (context.customerId && String(value.customer_id) !== String(context.customerId)) ||
    (context.opportunityId && String(value.opportunity_id) !== String(context.opportunityId));
  if (mismatch) throw Object.assign(Error('建议与当前资料或页签不匹配，请重新读取'), {code:'ADVICE_CONTEXT_MISMATCH'});
  const state = {id:value.id, status:value.status, summary:'', rows:[]};
  if (value.stale || value.status === 'superseded') return {...state, status:'stale'};
  if (value.status === 'succeeded') return adviceResult(value);
  if (['queued', 'running'].includes(value.status)) return state;
  if (value.status === 'failed') return {...state, status:'error', error:value.error || '本次分析未完成，可主动重新生成'};
  throw Object.assign(Error('建议状态无法确认，请重新读取'), {code:'ADVICE_CONTEXT_MISMATCH'});
}

module.exports = {adviceAllowed, adviceState};
