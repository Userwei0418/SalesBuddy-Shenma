const TYPE_NAMES = { prospect: "潜在客户", opportunity: "商机客户", won: "已成单客户" };
function dateValue(value) {
  if (!value) return "";
  const text = String(value);
  // Date-only and legacy timezone-less values already describe the Beijing date.
  if (/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$/.test(text)) return text.slice(0, 10);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 10);
}
function today() { return dateValue(new Date().toISOString()); }
// Only use for a new review, never for restored drafts or saved records:
// an empty date there may be an intentional user edit.
function withDefaultDates(values, reference = today()) {
  const result = { ...values };
  for (const key of ['interaction_at', 'created_date']) {
    if (result[key] == null || String(result[key]).trim() === '') result[key] = reference;
  }
  return result;
}
function dateLabel(value) {
  const date = dateValue(value);
  return date ? `${date.slice(0,4)}年${Number(date.slice(5,7))}月${Number(date.slice(8,10))}日` : "";
}
function withinSevenDays(value, reference = today()) {
  const date = dateValue(value);
  if (!date) return null;
  const days = (Date.parse(reference) - Date.parse(date)) / 86400000;
  return days >= 0 && days <= 6;
}
function sevenLabel(value) { return value === true ? "是" : value === false ? "否" : "未判断"; }
function typeLabel(value) { return TYPE_NAMES[value] || value || "未记录"; }
module.exports = { dateValue, today, withDefaultDates, dateLabel, withinSevenDays, sevenLabel, typeLabel };
