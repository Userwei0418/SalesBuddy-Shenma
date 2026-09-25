// Task recipients are company colleagues; the directory does not grant CRM access.
async function allTaskRecipients(api, current = () => true) {
  const items = [], seen = new Set();
  let offset = 0;
  do {
    const page = await api.listTaskRecipients({page_size:100,offset});
    if (!current()) return null;
    if (!page || !Array.isArray(page.items)) throw Error('人员目录数据不完整，请重试');
    for (const person of page.items) {
      if (!person.id || seen.has(person.id)) throw Error('人员目录已变化，请重新加载');
      seen.add(person.id); items.push(person);
    }
    if (!page.has_more) return items;
    if (!Number.isInteger(page.next_offset) || page.next_offset <= offset || !page.items.length) {
      throw Error('人员目录分页异常，请重试');
    }
    offset = page.next_offset;
  } while (current());
  return null;
}
module.exports = {allTaskRecipients};
