// Keep a prefix of controls visible; reserve the overflow trigger only when needed.
export function visibleControlCount(width, widths, searchWidth = 0, actionsWidth = 0, gap = 8, moreWidth = 112) {
  const fixed = searchWidth + actionsWidth + (searchWidth ? gap : 0) + (actionsWidth ? gap : 0);
  const total = widths.reduce((sum, value) => sum + value, 0) + Math.max(0, widths.length - 1) * gap;
  if (total + fixed <= width) return widths.length;
  let used = fixed + moreWidth, count = 0;
  for (const itemWidth of widths) {
    if (used + itemWidth + gap > width) break;
    used += itemWidth + gap;
    count++;
  }
  return count;
}
