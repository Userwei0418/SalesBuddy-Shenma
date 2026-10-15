// Only bounded routing references and original selectors. No report facts,
// prose, credentials or provider results are persisted by the side panel.
import {stable} from '../../work-report/state.js';
const PREFIX='sales-web:review-report:v1:',AGE=7*86400000;
const uuid=value=>typeof value==='string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value);
export const referenceKey=(owner,selection,mode)=>PREFIX+encodeURIComponent(stable({owner,selection,mode}));
export function readReference(key,owner,storage=globalThis.sessionStorage,now=Date.now()){
  try{
    const row=JSON.parse(storage?.getItem(key) || 'null');
    if(!row || row.owner!==owner)return null;
    if(!['daily','weekly'].includes(row.mode) || !Number.isFinite(row.expiresAt) || row.expiresAt<=now || row.expiresAt>now+AGE || ['snapshotId','runId','documentId'].some(field=>row[field]!=null && !uuid(row[field])) || !row.plan?.selection || !uuid(row.plan.scheme?.scheme_id) || !Number.isInteger(row.plan.scheme.version_no)){storage.removeItem(key);return null;}
    return row;
  }catch{return null;}
}
export function saveReference(key,owner,row,storage=globalThis.sessionStorage,now=Date.now()){
  try{
    if(!row){storage?.removeItem(key);return;}
    storage?.setItem(key,JSON.stringify({owner,mode:row.mode,plan:row.plan,snapshotId:row.snapshotId || null,runId:row.runId || null,documentId:row.documentId || null,pending:row.pending || null,expiresAt:now+AGE}));
    const own=Array.from({length:storage.length},(_,i)=>storage.key(i)).filter(item=>item?.startsWith(PREFIX));
    if(own.length>32)for(const old of own.filter(item=>item!==key).slice(0,own.length-32))storage.removeItem(old);
  }catch{/* A disabled session store only prevents navigation recovery. */}
}
