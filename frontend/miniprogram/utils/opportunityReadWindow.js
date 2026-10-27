// Refresh only the already displayed opportunity window, without hiding it.
// This is a bounded read; normal scrolling still fetches one 20-row page.
async function readOpportunityWindow(read, params, limit, current, onChunk) {
  const items = [], ids = new Set();
  let offset = 0, first, last;
  do {
    const response = await read({...params,includeOverview:false,pageSize:Math.min(100,limit-items.length),offset});
    if (!current()) return null;
    if (!response || !Array.isArray(response.items) || !response.summary ||
        !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean')
      throw Error('商机分页数据不完整');
    if (first && first.summary.total !== response.summary.total)
      throw Error('商机列表正在变化，请刷新列表');
    if (response.has_more && (!response.items.length || response.next_offset !== offset + response.items.length))
      throw Error('商机列表分页未继续，请重试');
    for (const row of response.items) {
      if (!row.id || ids.has(row.id)) throw Error('商机列表正在变化，请刷新列表');
      ids.add(row.id);items.push(row);
    }
    first = first || response;last = response;offset = response.next_offset;
    onChunk(response.items);
  } while (last.has_more && items.length < limit);
  return {...first,items,has_more:last.has_more,next_offset:last.next_offset};
}

module.exports = {readOpportunityWindow};
