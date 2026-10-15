// Shared presentation rules for the saved board and its table editor.
// Business values, permissions and full-result ordering remain server-owned.
import {dateFilterError} from './opportunity-scope-date.js';
export const BOARD_ROUTE = 'pages/opportunities/index';
export const EDITOR_ROUTE = 'pages/workbench/index';
export const copy = value => JSON.parse(JSON.stringify(value));
export const stable = value => JSON.stringify(canonical(value));
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().filter(key => value[key] !== undefined).map(key => [key, canonical(value[key])]));
  return value;
}
export const clampWidth = width => Math.max(80, Math.min(600, Math.round(Number(width) || 160)));
export const fieldLabel = field => field.type !== 'money' ? field.label : field.label.includes('万元') ? field.label : /[（(]元[）)]/.test(field.label) ? field.label.replace(/[（(]元[）)]/g, '（万元）') : `${field.label}（万元）`;

export function moveColumn(columns, from, to) {
  const source = columns.findIndex(column => column.key === from);
  const target = columns.findIndex(column => column.key === to);
  if (source < 0 || target < 0 || source === target) return columns;
  const next = columns.slice();
  const [column] = next.splice(source, 1);
  next.splice(target, 0, column);
  return next;
}

export function configureColumns(config, keys, fields) {
  const selected = [...new Set(keys.includes('name') ? keys : ['name', ...keys])];
  const existing = new Map(config.columns.map(column => [column.key, column]));
  const catalog = new Map(fields.map(field => [field.key, field]));
  return {...config, columns: selected.filter(key => catalog.has(key)).map(key => existing.get(key) || {key, width: clampWidth(catalog.get(key).width)})};
}

export function presentationError(config, fields) {
  const known = new Map(fields.map(field => [field.key, field]));
  if (config?.schema_version !== 2 || !Array.isArray(config.columns) || !config.columns.length) return '方案格式不可用，请重新配置';
  const dateError = dateFilterError(config.date_filter);
  if (dateError) return dateError;
  try {if (configStorageBytes(config) > 32768) return '方案内容过多，请减少筛选项或文本长度';} catch (error) {return error.message;}
  const missing = config.columns.filter(column => !known.has(column.key));
  if (missing.length) return `方案中的字段已不可用：${missing.map(column => column.key).join('、')}。请到商机经营重新选择字段`;
  if (config.filters?.some(filter => !known.has(filter.field))) return '方案中的筛选字段已不可用，请编辑方案';
  if (config.sorts?.some(sort => !known.get(sort.field)?.sortable)) return '方案中的排序字段已不可用，请编辑方案';
  if (config.group_by && !known.get(config.group_by)?.groupable) return '方案中的分组字段已不可用，请编辑方案';
  return '';
}

// Match the backend's UTF-8 jsonb storage projection, including separators.
// This is immediate UI feedback; the server still validates every query/save.
export function configStorageBytes(config) {
  const serialize = value => {
    if (typeof value === 'string' && (value.includes('\0') || value.isWellFormed && !value.isWellFormed())) throw Error('配置包含无效字符，请重新输入');
    if (Array.isArray(value)) return '[' + value.map(serialize).join(', ') + ']';
    if (value && typeof value === 'object') return '{' + Object.entries(value).filter(([, item]) => item !== null && item !== undefined).map(([key, item]) => JSON.stringify(key) + ': ' + serialize(item)).join(', ') + '}';
    return JSON.stringify(value);
  };
  const normalized = {q:'',filters:[],sorts:[{field:'status',direction:'asc'},{field:'expected_close_date',direction:'asc'}],scope:{kind:'authorized'},period:{kind:'current_quarter'},density:'comfortable',wrap:false,...config,columns:config.columns.map(column=>({width:160,...column}))};
  return new TextEncoder().encode(serialize(normalized)).length;
}

export function querySignature(config) {
  // Resizing/dragging/wrapping does not change business membership or order.
  const {columns, density, wrap, ...query} = config;
  return stable({...query, columns: columns.map(column => column.key).sort()});
}

export function appendPage(previous, result, append) {
  if (!Array.isArray(result?.items) || !Number.isInteger(result.total) || result.total < 0) throw Error('商机列表响应不完整，请重试');
  const rows = append ? [...previous, ...result.items] : result.items;
  if (rows.some(row => !row.id) || new Set(rows.map(row => row.id)).size !== rows.length) throw Error('商机列表在翻页期间发生变化，请刷新后重试');
  if (result.has_more && (!Number.isInteger(result.next_offset) || result.next_offset < rows.length)) throw Error('分页位置无效，请刷新后重试');
  return rows;
}

export function tableRows(rows, groupBy, fields = [], groups = []) {
  if (!groupBy) return rows;
  const result = [];
  let last = Symbol('first');
  for (const row of rows) {
    const key = row.group_key ?? null;
    if (last !== key) {
      const label = fields.find(field => field.key === groupBy)?.options?.find(option => option.value === key)?.label || row.group_label || '未填写';
      const summary = groups.find(group => group.key === key);
      result.push({id: `group:${result.length}:${key}`, __group: true, label, summary});
    }
    result.push(row);
    last = key;
  }
  return result;
}

export function formatValue(value, field) {
  if (value === undefined || value === null || value === '') return '—';
  if (Array.isArray(value)) return value.length ? value.map(item => formatValue(item, {...field, type: 'text'})).join('、') : '—';
  if (typeof value === 'object') return value.label || value.name || value.display_name || '—';
  if (field.type === 'boolean') return value ? '是' : '否';
  const option = field.options?.find(item => String(item.value) === String(value));
  if (option) return option.label;
  if (field.type === 'money') {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return '—';
    try {
      const [whole, fraction] = decimalShift(value, -4).split('.');
      return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? `.${fraction}` : '');
    } catch (_) {return '—';}
  }
  return String(value);
}

// Convert a yuan decimal to/from the editor's ten-thousand-yuan input without
// rounding a genuine zero away or losing cents through binary multiplication.
export function decimalShift(value, digits) {
  if (value === null || value === undefined || value === '') return '';
  const raw = String(value).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(raw)) throw Error('请输入有效金额');
  const negative = raw.startsWith('-');
  const [whole, fraction = ''] = raw.replace(/^-/, '').split('.');
  const target = whole.length + digits;
  const joined = whole + fraction;
  const number = target <= 0 ? `0.${'0'.repeat(-target)}${joined}` : target >= joined.length ? joined + '0'.repeat(target - joined.length) : `${joined.slice(0, target)}.${joined.slice(target)}`;
  const trimmed = number.replace(/^0+(?=\d)/, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  return `${negative && Number(trimmed) !== 0 ? '-' : ''}${trimmed}`;
}

export function editValue(value, field) {
  if (field.type === 'money') return decimalShift(value, -4);
  if (field.type === 'datetime') return value && Number.isFinite(Date.parse(value)) ? new Date(Date.parse(value) + 28800000).toISOString().slice(0, 19) : '';
  if (field.type === 'person') return value?.id ?? value ?? null;
  if (field.type === 'people') return (value || []).map(item => item.id ?? item.user_id ?? item);
  return value ?? null;
}

export function saveValue(value, field) {
  if (value === undefined || value === null || value === '') return null;
  if (field.type === 'money') return decimalShift(value, 4);
  if (field.type === 'number') {
    const number = Number(value);
    if (!Number.isSafeInteger(number)) throw Error('请输入有效整数');
    return number;
  }
  if (field.type === 'datetime') {
    if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value)) throw Error('请选择有效时间');
    return `${value.length === 16 ? value + ':00' : value}+08:00`;
  }
  return value;
}

// Editable fields carry their own nullability and length contract. Filters use
// saveValue separately: a required business field can still have an empty filter.
export function saveEditValue(value, field) {
  const empty = value == null || typeof value === 'string' && !value.trim();
  if (field.nullable === false && empty) throw Error(`请填写${field.label}`);
  if (typeof value === 'string' && field.max_length && [...value].length > field.max_length) throw Error(`${field.label}最多填写 ${field.max_length} 字`);
  return saveValue(value, field);
}

export const rowCanEdit = (row, field) => field.editable === true && field.read_only !== true && (row?.capabilities?.editable_fields || []).includes(field.key);
export const rowCanEditInline = (row, field) => field.edit_mode === 'inline' && rowCanEdit(row, field);
export const emptyOperator = operator => ['is_empty', 'is_not_empty'].includes(operator);
export function normalizeFilter(filter) {
  if (emptyOperator(filter.operator)) return {field: filter.field, operator: filter.operator};
  const values = Array.isArray(filter.value) ? filter.value : [filter.value];
  if (!values.length || values.some(value => value === null || value === undefined || value === '')) throw Error('请补全筛选条件，或选择“未填写”');
  if (filter.operator === 'between' && values.length !== 2) throw Error('请填写范围的起点与终点');
  return filter;
}
export function editFilter(filter, fields) {
  const field = fields.find(item => item.key === filter.field);
  if (emptyOperator(filter.operator) || !field) return {...filter};
  const convert = value => editValue(value, field);
  return {...filter, value: Array.isArray(filter.value) ? filter.value.map(convert) : convert(filter.value)};
}
export function saveFilter(filter, fields) {
  if (emptyOperator(filter.operator)) return normalizeFilter(filter);
  const field = fields.find(item => item.key === filter.field);
  const convert = value => saveValue(value, field);
  return normalizeFilter({...filter, value: Array.isArray(filter.value) ? filter.value.map(convert) : convert(filter.value)});
}

// Reject late reads after filter changes, logout, or switching companies.
export class LatestRead {
  generation = 0;
  invalidate() { this.generation += 1; }
  async run(owner, currentOwner, request) {
    const generation = ++this.generation;
    try {
      const value = await request();
      return {current: generation === this.generation && owner === currentOwner(), value};
    } catch (error) {
      if (generation !== this.generation || owner !== currentOwner()) return {current: false};
      throw error;
    }
  }
}
