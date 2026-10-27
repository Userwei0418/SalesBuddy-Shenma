// Remember an uncertain upload's UUID across drawer reentry/tab refresh. Store
// only opaque hashes/keys, never bytes, authentication or user-entered content.
const memory = new Map();
async function digest(bytes) {
  const value = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(value), byte => byte.toString(16).padStart(2, '0')).join('');
}
export async function prepareUploadIntent(file, actor, storage) {
  if (!actor) throw Error('请先登录');
  if (storage === undefined) { try { storage = globalThis.sessionStorage; } catch { /* restricted storage: keep the in-memory intent */ } }
  if (!file?.size || file.size > 50 * 1024 * 1024) throw Error('附件需大于 0 且不超过 50 MiB');
  const hash = await digest(await file.arrayBuffer());
  const slot = 'sales-web:knowledge-upload:' + await digest(new TextEncoder().encode(JSON.stringify([actor, file.name, file.type, file.size, hash])));
  let key = memory.get(slot);
  try { key ||= storage?.getItem(slot); } catch { /* private storage may be unavailable */ }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(key || '')) key = globalThis.crypto.randomUUID();
  memory.set(slot, key);
  try { storage?.setItem(slot, key); } catch { /* keep the in-memory intent */ }
  return {slot, key, storage};
}
export function completeUploadIntent(intent) {
  if (memory.get(intent.slot) === intent.key) memory.delete(intent.slot);
  try { if (intent.storage?.getItem(intent.slot) === intent.key) intent.storage.removeItem(intent.slot); } catch { /* no persistent entry */ }
}
