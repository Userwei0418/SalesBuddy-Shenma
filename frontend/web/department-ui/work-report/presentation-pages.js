// Presentation only: page existing records/text without calculating business facts.
export function rowPages(rows, size = 6) {
  if (!rows.length) return [[]];
  return Array.from({length: Math.ceil(rows.length / size)}, (_, index) => rows.slice(index * size, (index + 1) * size));
}

export function textPages(text, limit = 1000) {
  const chars = Array.from(text || '');
  if (!chars.length) return [''];
  const pages = [];
  while (chars.length > limit) {
    let end = limit;
    for (let i = limit - 1; i >= Math.floor(limit * 0.6); i--) {
      if (/[\n。！？；]/u.test(chars[i])) { end = i + 1; break; }
    }
    pages.push(chars.splice(0, end).join(''));
  }
  if (chars.length) pages.push(chars.join(''));
  return pages;
}

export function paragraphPages(items, limit = 1000) {
  const pages = []; let page = [], length = 0;
  for (const item of items) {
    const parts = textPages(item.text, limit);
    parts.forEach((text, index) => {
      if (page.length && (length + Array.from(text).length > limit || page.length >= 3)) {
        pages.push(page); page = []; length = 0;
      }
      const part={...item, text, continued: index > 0};
      if(index>0)delete part.source_notes;
      page.push(part); length += Array.from(text).length;
    });
  }
  if (page.length) pages.push(page);
  return pages.length ? pages : [[]];
}

export function presentationDirection(event) {
  if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return 0;
  if (event.target?.closest?.('input,textarea,select,summary,[contenteditable],[role="combobox"],[role="slider"],[role="tablist"],[data-presentation-interactive]')) return 0;
  if (event.key === ' ' && event.target?.closest?.('button,a')) return 0;
  return ['ArrowRight','PageDown',' '].includes(event.key) ? 1 : ['ArrowLeft','PageUp'].includes(event.key) ? -1 : 0;
}
