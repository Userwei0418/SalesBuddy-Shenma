// A complete picker/filter list must finish server pagination before it is shown.
async function collectPages(fetchPage, cancelled = () => false) {
  const items = [], seen = new Set();
  let offset = 0;
  while (!cancelled()) {
    const page = await fetchPage(offset);
    if (cancelled()) return null;
    if (!page || !Array.isArray(page.items) || typeof page.has_more !== 'boolean') throw new Error('列表分页数据不完整，请重试');
    for (const item of page.items) {
      if (!item.id || seen.has(item.id)) throw new Error('商机列表正在变化，请重新加载');
      seen.add(item.id); items.push(item);
    }
    if (!page.has_more) return { items };
    if (!page.items.length || page.next_offset !== offset + page.items.length) throw new Error('列表分页未继续，请重试');
    offset = page.next_offset;
  }
  return null;
}
module.exports = { collectPages };
