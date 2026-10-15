// Presentation state only. Report facts, coverage and permissions are server-owned.
import {periodRange, todayKey} from '../pc/period.js';

export const PERIOD_CHOICES = [
  {value:'day',label:'日报'}, {value:'week',label:'周报'}, {value:'biweek',label:'双周报'},
  {value:'month',label:'月报'}, {value:'quarter',label:'季度报'},
];
export const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
export function stable(value) {
  const normalize = item => Array.isArray(item) ? item.map(normalize) : item && typeof item === 'object'
    ? Object.fromEntries(Object.keys(item).sort().filter(key=>item[key] !== undefined).map(key=>[key,normalize(item[key])])) : item;
  return JSON.stringify(normalize(value));
}
export function calendarHint(kind, anchor = todayKey()) {
  if (!PERIOD_CHOICES.some(item=>item.value===kind) || !/^\d{4}-\d{2}-\d{2}$/.test(anchor)) return '';
  const timestamp = Date.parse(`${anchor}T12:00:00+08:00`);
  if (!Number.isFinite(timestamp) || todayKey(timestamp) !== anchor) return '';
  const range = periodRange(kind, timestamp);
  return `${range.start} — ${range.end}`;
}
export function isRunning(status) { return ['queued','running','preparing','generating','validating','cancelling'].includes(status); }
export function safeFilename(title) { return String(title || '工作报告').replace(/[\\/:*?"<>|\u0000-\u001f]/g,'_').slice(0,120); }
export function appendSources(previous, page) {
  if (!Array.isArray(page.items)) throw Error('来源列表响应不完整，请重试');
  const ids = new Set(previous.map(item=>item.ref));
  const next = [...previous];
  for (const item of page.items) {
    if (!item.ref || ids.has(item.ref)) throw Error('来源列表已变化，请重新读取');
    ids.add(item.ref); next.push(item);
  }
  if (page.hasMore && (!page.items.length || page.nextOffset == null)) throw Error('来源读取尚未完成，请重新读取');
  return next;
}

// A request result may only update the currently opened object and identity.
export class ReadSequence {
  constructor() { this.serial=0; }
  invalidate() { this.serial++; }
  async run(owner, identity, loader) {
    const serial=++this.serial;
    try {
      const value=await loader();
      return {current:serial===this.serial && owner===identity(),value};
    } catch (error) {
      if(serial!==this.serial || owner!==identity()) return {current:false};
      throw error;
    }
  }
}
