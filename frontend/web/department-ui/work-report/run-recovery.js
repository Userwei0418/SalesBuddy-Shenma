// A bounded navigation reference, never a task status, credential or report cache.
// The server reauthorizes every status read. Expiry only forgets this local link.
const KEY='sales-web:work-report:run:v1';
const MAX_AGE=7*24*60*60*1000;
const uuid=value=>typeof value==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export function readRunReference(owner,storage,now=Date.now()) {
  try {
    storage ||= globalThis.sessionStorage;
    const value=JSON.parse(storage?.getItem(KEY) || 'null');
    if(!value || value.owner!==owner || !owner)return null;
    if(!uuid(value.id) || (value.documentId!==null && !uuid(value.documentId)) || !Number.isFinite(value.expiresAt) || value.expiresAt<=now || value.expiresAt>now+MAX_AGE){storage.removeItem(KEY);return null;}
    return {id:value.id,documentId:value.documentId};
  }catch{return null;}
}
export function saveRunReference(owner,run,storage,now=Date.now()) {
  if(!owner)return;
  try {
    storage ||= globalThis.sessionStorage;
    if(!run){if(JSON.parse(storage?.getItem(KEY) || 'null')?.owner===owner)storage.removeItem(KEY);return;}
    if(!uuid(run.id) || (run.documentId!=null && !uuid(run.documentId)))return;
    const previous=readRunReference(owner,storage,now);
    if(previous?.id===run.id && previous.documentId===(run.documentId || null))return;
    storage?.setItem(KEY,JSON.stringify({owner,id:run.id,documentId:run.documentId || null,expiresAt:now+MAX_AGE}));
  }catch{/* Storage may be disabled; the current run URL still works. */}
}
