import {decimalShift} from '../pc/opportunity-workspace.js';

// Display only. Never derive opening balances, changes, totals or membership
// from the visible (possibly paginated) rows.
export const MEETING_MONEY = [
  {id:'acv',label:'ACV'},
  {id:'recognized',label:'预计确收'},
  {id:'collection',label:'预计回款'},
];
export function meetingAmount(value,{signed=false}={}) {
  if(typeof value!=='string' || !/^-?\d+(?:\.\d+)?$/.test(value))return '—';
  const shifted=decimalShift(value,-4);
  const nonzero=/[1-9]/.test(shifted),positive=nonzero && !shifted.startsWith('-');
  const [whole,fraction]=shifted.split('.');
  return `${signed && positive?'+':''}${whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')}${fraction?'.'+fraction:''}`;
}
export function changeTone(value) {
  if(typeof value!=='string' || !/^-?\d+(?:\.\d+)?$/.test(value) || !/[1-9]/.test(value))return 'neutral';
  return value.startsWith('-')?'decrease':'increase';
}
export function countText(value) {return Number.isSafeInteger(value) && value>=0?String(value):'—';}
