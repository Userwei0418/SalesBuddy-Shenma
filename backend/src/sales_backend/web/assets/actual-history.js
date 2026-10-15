import {esc, table, jsonBlock} from './core.js';
import {yuan} from './opportunity-finance.js';

const kinds = {recognized:'实际确收', collection:'实际回款'};
const statuses = {missing:'未登记', single_source:'旧来源待核对', confirmed:'已确认', partial:'部分登记', withdrawn:'已撤回', needs_confirmation:'待核对', stale_confirmation:'参考已变化，待复核'};
const sources = {operations:'运营登记', entries:'逐笔明细', historical:'历史汇总'};
const taxes = {exclusive:'不含税', inclusive:'含税', not_applicable:'不适用', unknown:'待核实'};
const savedText = value => value == null ? '未记录' : value === '' ? '空白' : typeof value === 'object' ? '结构化记录（展开完整记录查看）' : String(value);
const savedLabel = (labels, value) => value == null || value === '' ? savedText(value)
  : Object.hasOwn(labels, value) ? labels[value] : `未识别（${savedText(value)}）`;

function confirmationText(value) {
  if (!Array.isArray(value.confirmations)) return '未记录明细';
  if (!value.confirmations.length) return '无逐项记录';
  return value.confirmations.map(row => {
    if (!row || typeof row !== 'object') return '明细格式待核对（展开完整记录查看）';
    // Names come only from this change's saved snapshot, never a current catalog.
    const name = row.material_snapshot?.material_line?.catalog_snapshot?.name
      || (row.material_line_id ? `物料（当时未记录名称，ID：${savedText(row.material_line_id)}）`
        : value.kind === 'collection' ? '商机回款' : '物料（当时未记录名称）');
    return `${name}：${yuan(row.amount)}；${savedLabel(statuses, row.status)}${row.status === 'withdrawn' ? '（保留原值，不计入）' : ''}；依据：${savedText(row.source_ref)}；版本：${savedText(row.version_no)}`;
  }).join('\n');
}

function actualValues(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return [
    `${savedText(value.year)} 年 Q${savedText(value.quarter)}`,
    savedLabel(kinds, value.kind), yuan(value.amount), yuan(value.confirmed_subtotal),
    savedLabel(taxes, value.tax_basis), savedLabel(statuses, value.status),
    savedLabel(sources, value.source), savedText(value.version_no),
    savedText(value.decision?.reason), confirmationText(value),
  ];
}

export function actualHistoryBody(change) {
  const before = actualValues(change.before), after = actualValues(change.after);
  const labels = ['期间', '实际指标', '采用金额', '已登记小计', '税口径', '状态', '采用来源', '实际版本', '采用说明', '逐项登记'];
  return `<section class="actual-history"><h4>${esc(change.label || '运营期间实际')}</h4>` +
    (change.reason != null ? `<p>本次修改原因：${esc(savedText(change.reason))}</p>` : '') +
    table(['项目', '之前', '之后'], labels, (label, index) =>
      `<tr><th scope="row">${esc(label)}</th><td>${esc(before ? before[index] : savedText(change.before))}</td><td>${esc(after ? after[index] : savedText(change.after))}</td></tr>`) + '</section>';
}

export const savedHistoryDetails = change =>
  `<details><summary>查看当时保存的完整记录</summary>${jsonBlock(change)}</details>`;
