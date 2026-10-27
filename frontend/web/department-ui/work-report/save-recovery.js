import {stable} from './state.js';

// These responses do not establish whether the original revision committed.
export function uncertainSave(error) {
  const status=Number(error?.statusCode || error?.status);
  return status>=500 && status<=599 || error?.code==='NETWORK_ERROR' ||
    (!status && /timeout|超时/i.test(error?.message || ''));
}

// Compare the complete submitted content, including title and source notes.
// A readable older/shared revision is not proof of this save's outcome.
export function matchesSavedRevision(view, documentId, intent) {
  return Boolean(view?.id===documentId && view.revision?.document_id===documentId &&
    Number.isInteger(view.version_no) && view.version_no>intent.version_no &&
    view.latest_revision_id===view.revision.id && view.title===intent.content.title &&
    stable(view.revision.content)===stable(intent.content));
}
