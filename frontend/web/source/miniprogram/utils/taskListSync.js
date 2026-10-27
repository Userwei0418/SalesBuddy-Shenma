/** Offset-aware cache: unseen windows remain explicitly stale until read again. */
function createWindowCache(rows, total = rows.length) {
  return {slots: rows.map(row => ({...row, status: row.sourceStatus || row.status})), dirty: new Set(), total};
}

function cloneWindowCache(cache) {
  return {slots: cache.slots.slice(), dirty: new Set(cache.dirty), total: cache.total};
}

function applyWindow(cache, result, window, invalidateOutside = true) {
  const {offset, page_size: size} = window, total = result.summary.filtered_total;
  const oldIds = new Set(cache.slots.slice(offset, offset + size).filter(Boolean).map(row => row.id));
  const newIds = new Set(result.items.map(row => row.id));
  const changed = oldIds.size !== newIds.size || [...oldIds].some(id => !newIds.has(id));
  const totalChanged = cache.total !== total;
  cache.total = total;
  const length = Math.min(total, Math.max(cache.slots.length, offset + result.items.length));
  cache.slots.length = length;
  for (const index of cache.dirty) if (index >= length) cache.dirty.delete(index);
  for (let index = 0; index < length; index++) {
    if (index >= offset && index < offset + size) {
      cache.slots[index] = result.items[index - offset] || null;
      cache.dirty.delete(index);
    } else {
      if (totalChanged || (invalidateOutside && changed)) cache.dirty.add(index);
      // A moved item has only one visible card; its old slot must be read again.
      if (cache.slots[index] && newIds.has(cache.slots[index].id)) {
        cache.slots[index] = null;
        cache.dirty.add(index);
      }
    }
    if (!cache.slots[index]) cache.dirty.add(index);
  }
  return cache;
}

function repairWindow(cache) {
  if (!cache.dirty.size) return null;
  const offset = Math.min(...cache.dirty);
  return {offset, page_size: Math.min(100, cache.slots.length - offset)};
}

function projectWindowCache(cache, total) {
  const repair = repairWindow(cache);
  return {
    items: cache.slots.flatMap((row, index) => row ? [{...row, taskListPosition: index, taskSyncStale: cache.dirty.has(index)}] : []),
    has_more: Boolean(repair) || cache.slots.length < total,
    next_offset: repair ? repair.offset : cache.slots.length < total ? cache.slots.length : null,
  };
}

module.exports = {createWindowCache, cloneWindowCache, applyWindow, repairWindow, projectWindowCache};
