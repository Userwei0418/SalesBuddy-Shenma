const access = require('./access');
const opportunity = require('./opportunity');
const opportunityAmount = require('./opportunityAmount');
const { MESSAGES } = require('./errorMessages');

const keyFor = period => `${period.year}-Q${period.quarter}`;
const text = value => String(value == null ? '' : value).trim();
const decimal = value => typeof value === 'string' && /^\d+(\.\d+)?$/.test(value);
const canonical = value => {
  const raw = text(value);
  if (!/^\d+(\.\d+)?$/.test(raw)) return raw;
  const [whole, fraction = ''] = raw.split('.');
  return whole.replace(/^0+(?=\d)/, '') + (fraction.replace(/0+$/, '') ? `.${fraction.replace(/0+$/, '')}` : '');
};
const amountOf = value => { try { return canonical(opportunity.amount(value)); } catch (_) { return text(value); } };
const equalRow = (draft, item) => amountOf(draft.amount) === amountOf(opportunity.wan(item.amount))
  && text(draft.evidence) === text(item.source_ref) && text(draft.note) === text(item.note);
const data = {
  recognizedActualVisible: false, recognizedActualEditable: false, recognizedActualLoading: false,
  recognizedActualLoaded: false, recognizedActualError: '', recognizedActualQuarterOptions: [],
  recognizedActualQuarterIndex: 0, recognizedActualQuarterLabel: '', recognizedActualRows: [],
  recognizedActualCurrentText: '—', recognizedActualTotalText: '未登记', recognizedActualDirty: false,
  recognizedActualAnyDirty: false, recognizedActualReason: '', recognizedActualAttributionText: '',
  recognizedActualReplaceRequired: false, recognizedActualReplaceSource: false, recognizedActualNeedsReview: false,
};

function read(result, opportunityId, period) {
  const actual = result && result.actual;
  if (!result || result.opportunity_id !== opportunityId || !result.period || result.period.year !== period.year
      || result.period.quarter !== period.quarter || typeof result.can_edit !== 'boolean' || !actual
      || !/^[a-f0-9]{64}$/i.test(actual.revision || '') || typeof actual.requires_source_replacement !== 'boolean'
      || !(actual.amount === null || decimal(actual.amount)) || !(actual.known_subtotal === null || decimal(actual.known_subtotal))
      || !Array.isArray(result.items) || !Number.isInteger(actual.missing_material_count) || actual.missing_material_count < 0
      || !['frozen', 'unresolved', 'not_registered'].includes(actual.attribution_status)) {
    throw Error('实际确收信息与当前商机或季度不一致，请重新读取');
  }
  const ids = new Set();
  for (const item of result.items) {
    if (!item || typeof item.material_line_id !== 'string' || !item.material_line_id || ids.has(item.material_line_id)
        || typeof item.material_name !== 'string' || typeof item.material_code !== 'string'
        || typeof item.is_active !== 'boolean' || typeof item.eligible !== 'boolean'
        || !(item.amount === null || decimal(item.amount)) || typeof item.source_ref !== 'string'
        || typeof item.note !== 'string' || !['missing', 'confirmed', 'withdrawn'].includes(item.status)) {
      throw Error('实际确收物料信息不完整，请重新读取');
    }
    ids.add(item.material_line_id);
  }
  return result;
}
function draftFor(result) {
  return { revision: result.actual.revision, replaceSource: false,
    rows: Object.fromEntries(result.items.map(item => [item.material_line_id,
      { amount: opportunity.wan(item.amount), evidence: item.source_ref, note: item.note }])) };
}
function changedRows(draft, result) {
  return Object.entries(draft.rows || {}).filter(([id, row]) => {
    const item = result.items.find(value => value.material_line_id === id);
    return !item || !equalRow(row, item);
  });
}
function hasChanges(draft, result) { return changedRows(draft, result).length > 0; }
function fail(code, label, fallback) { return Object.assign(Error(`${label}：${MESSAGES[code] || fallback}`), { code }); }
function payload(drafts, snapshots) {
  return Object.entries(drafts || {}).sort(([a], [b]) => a.localeCompare(b)).flatMap(([key, draft]) => {
    const result = snapshots[key];
    if (!result) throw Error('实际确收尚未读取完成，请重新读取后保存');
    const changed = changedRows(draft, result);
    if (!changed.length) return [];
    const label = `${result.period.year} Q${result.period.quarter}`;
    if (!result.can_edit) throw fail('OPPORTUNITY_RECOGNIZED_FORBIDDEN', label, '当前无权修改实际确收');
    if (draft.revision !== result.actual.revision) throw Error(`${label}：实际确收已有更新，请核对最新金额和依据，再勾选确认`);
    if (result.actual.requires_source_replacement && draft.replaceSource !== true) throw fail('OPPORTUNITY_RECOGNIZED_SOURCE_CONFIRMATION_REQUIRED', label, '请确认替换原有实际确收来源');
    const entries = changed.map(([id, row]) => {
      const item = result.items.find(value => value.material_line_id === id);
      if (!item || !item.eligible) throw Error(`${label}：物料已变化或不允许在本商机登记，请刷新状态后核对`);
      const name = item.material_name || item.material_code || '物料';
      if (!text(row.amount)) throw Error(`${label} · ${name}：请填写累计不含税确收金额，金额为零请填 0`);
      const amount = opportunity.amount(row.amount), evidence = text(row.evidence), note = text(row.note);
      if (!evidence || [...evidence].length > 160) throw Error(`${label} · ${name}：请填写确收依据，最多 160 字`);
      if ([...note].length > 1000) throw Error(`${label} · ${name}：备注最多 1000 字`);
      return { material_line_id: id, amount: String(amount), source_ref: evidence, note };
    });
    return [{ year: result.period.year, quarter: result.period.quarter, expected_actual_revision: result.actual.revision,
      ...(result.actual.requires_source_replacement ? { replace_source: true } : {}), entries }];
  });
}
// Decimal addition without Number/BigInt, including historical read precision.
function sumAmounts(values) {
  const scale = Math.max(2, ...values.map(value => (value.split('.')[1] || '').length));
  let sum = '0';
  for (const value of values) {
    const [whole, fraction = ''] = value.split('.'), digits = whole + fraction.padEnd(scale, '0');
    let out = '', carry = 0;
    for (let a = sum.length - 1, b = digits.length - 1; a >= 0 || b >= 0 || carry; a--, b--) {
      const n = Number(sum[a] || 0) + Number(digits[b] || 0) + carry;
      out = String(n % 10) + out; carry = Math.floor(n / 10);
    }
    sum = out;
  }
  sum = sum.padStart(scale + 1, '0');
  return canonical(`${sum.slice(0, -scale)}.${sum.slice(-scale)}`);
}
function currentText(actual) {
  if (actual.amount !== null) return `${opportunity.wan(actual.amount)} 万元 · 已登记`;
  if (actual.known_subtotal !== null) return `${opportunity.wan(actual.known_subtotal)} 万元 · 已知小计`;
  return ({ missing: '未登记', withdrawn: '已撤回', needs_confirmation: '来源待核对', stale_confirmation: '原登记待核对', partial: '部分登记，尚无可用金额' })[actual.status] || '暂无可用金额';
}
function totalText(rows) {
  const entered = rows.filter(row => row.eligible && text(row.amount) !== '');
  if (!entered.length) return '未登记';
  try {
    const amounts = entered.map(row => String(opportunity.amount(row.amount)));
    const missing = rows.filter(row => row.eligible && text(row.amount) === '').length;
    return `${opportunity.wan(sumAmounts(amounts))} 万元${missing ? ` · 已填小计，另 ${missing} 项未登记` : ' · 已填合计'}`;
  } catch (_) { return '请先检查各项金额'; }
}
function create(api, session) {
  const active = component => !component.closed && component.data.recognizedActualVisible && component.recognizedActualOwner === access.identity(session());
  return {
    resetRecognizedActuals() {
      this.recognizedActualSequence = (this.recognizedActualSequence || 0) + 1;
      this.recognizedActualSnapshots = {}; this.recognizedActualReadErrors = {};
      this.recognizedActualOwner = access.identity(session());
      const visible = !!(this.properties.existing && this.properties.existing.id && !this.properties.visitContext);
      const options = this.data.quarterOptions || [], now = opportunity.quarterNow();
      const index = Math.max(0, options.findIndex(row => row.year === now.year && row.quarter === now.quarter));
      this.setData({ ...data, recognizedActualVisible: visible, recognizedActualQuarterOptions: options,
        recognizedActualQuarterIndex: index, 'form.recognized_actual_drafts': {} });
      if (visible) return this.loadRecognizedActual();
    },
    async loadRecognizedActual(force = false) {
      if (!active(this)) return;
      const period = this.data.recognizedActualQuarterOptions[this.data.recognizedActualQuarterIndex];
      if (!period) return;
      const key = keyFor(period), id = this.properties.existing.id, source = this.sourceKey;
      const sequence = this.recognizedActualSequence = (this.recognizedActualSequence || 0) + 1;
      const current = () => active(this) && source === this.sourceKey && sequence === this.recognizedActualSequence;
      const cached = this.recognizedActualSnapshots[key];
      if (cached && !force && !this.recognizedActualReadErrors[key]) return this.showRecognizedActual(cached);
      this.recognizedActualReadErrors[key] = '实际确收正在重新读取，请稍后保存';
      this.setData({ recognizedActualLoading: true, recognizedActualLoaded: false, recognizedActualEditable: false,
        recognizedActualQuarterLabel: period.label, recognizedActualError: '', recognizedActualRows: [], recognizedActualCurrentText: '—' });
      try {
        const result = read(await api.getOpportunityRecognizedActuals(id, period), id, period);
        if (!current()) return;
        const previous = this.recognizedActualSnapshots[key], drafts = this.data.form.recognized_actual_drafts || {};
        if (previous && previous.actual.revision !== result.actual.revision && drafts[key]) {
          const draft = drafts[key];
          // Keep only intentional edits. Untouched rows refresh to the new fact;
          // dirty values survive, but require explicit review of this revision.
          const rows = { ...draftFor(result).rows };
          for (const [rowId, row] of changedRows(draft, previous)) rows[rowId] = row;
          this.setData({ 'form.recognized_actual_drafts': { ...drafts, [key]: { ...draft, rows, replaceSource: false } } });
        }
        this.recognizedActualSnapshots[key] = result;
        delete this.recognizedActualReadErrors[key];
        this.showRecognizedActual(result);
      } catch (error) {
        if (current()) { this.recognizedActualReadErrors[key] = error.message || '实际确收读取失败，请重试'; this.setData({ recognizedActualError: this.recognizedActualReadErrors[key] }); }
      } finally { if (current()) this.setData({ recognizedActualLoading: false }); }
    },
    showRecognizedActual(result) {
      const key = keyFor(result.period), drafts = this.data.form.recognized_actual_drafts || {};
      const draft = drafts[key] || draftFor(result), dirty = hasChanges(draft, result);
      const rows = result.items.map(item => ({ ...item, ...draft.rows[item.material_line_id],
        currentEvidence: item.source_ref, currentNote: item.note,
        dirty: !equalRow(draft.rows[item.material_line_id] || draftFor(result).rows[item.material_line_id], item),
        currentText: item.amount === null ? item.status === 'withdrawn' ? '已撤回' : '未登记' : `${opportunity.wan(item.amount)} 万元`,
        amountPreview: opportunityAmount.previewWan(draft.rows[item.material_line_id]?.amount),
      }));
      this.setData({ recognizedActualLoaded: true, recognizedActualLoading: false, recognizedActualEditable: result.can_edit,
        recognizedActualError: '', recognizedActualQuarterLabel: `${result.period.year} Q${result.period.quarter}`,
        recognizedActualRows: rows, recognizedActualCurrentText: currentText(result.actual), recognizedActualTotalText: totalText(rows),
        recognizedActualDirty: dirty, recognizedActualAnyDirty: Object.entries(drafts).some(([draftKey, value]) => this.recognizedActualSnapshots[draftKey] && hasChanges(value, this.recognizedActualSnapshots[draftKey])),
        recognizedActualReason: !result.editable_reason ? '' : Object.prototype.hasOwnProperty.call(MESSAGES, result.editable_reason)
          ? MESSAGES[result.editable_reason] : '当前实际确收暂不可编辑，请刷新状态后重试', recognizedActualAttributionText: result.actual.attribution_status === 'unresolved' ? '历史归属待核对，不按当前认领人推算历史业绩。' : '',
        recognizedActualReplaceRequired: dirty && result.actual.requires_source_replacement,
        recognizedActualReplaceSource: draft.replaceSource === true, recognizedActualNeedsReview: dirty && draft.revision !== result.actual.revision,
      });
    },
    recognizedActualQuarter(event) {
      if (!active(this) || this.properties.disabled) return;
      const index = Number(event.detail.value);
      if (!Number.isInteger(index) || !this.data.recognizedActualQuarterOptions[index]) return;
      this.setData({ recognizedActualQuarterIndex: index }); return this.loadRecognizedActual();
    },
    recognizedActualInput(event) {
      if (!active(this) || this.properties.disabled || this.data.recognizedActualLoading || !this.data.recognizedActualEditable) return;
      const { key, id } = event.currentTarget.dataset;
      if (!['amount', 'evidence', 'note', 'replaceSource', 'reviewed'].includes(key)) return;
      const period = this.data.recognizedActualQuarterOptions[this.data.recognizedActualQuarterIndex], periodKey = keyFor(period);
      const result = this.recognizedActualSnapshots[periodKey]; if (!result) return;
      const drafts = { ...(this.data.form.recognized_actual_drafts || {}) }, draft = drafts[periodKey] || draftFor(result);
      const checked = event.detail.value === true || Array.isArray(event.detail.value) && event.detail.value.includes('confirm');
      if (key === 'replaceSource') drafts[periodKey] = { ...draft, replaceSource: checked };
      else if (key === 'reviewed') { if (!checked) return; drafts[periodKey] = { ...draft, revision: result.actual.revision }; }
      else {
        if (!result.items.some(item => item.material_line_id === id && item.eligible)) return;
        drafts[periodKey] = { ...draft, rows: { ...draft.rows, [id]: { ...draft.rows[id], [key]: event.detail.value == null ? '' : String(event.detail.value) } } };
      }
      this.setData({ 'form.recognized_actual_drafts': drafts }); this.showRecognizedActual(result); this.emit();
    },
    resetRecognizedActualDraft() {
      if (!active(this) || this.properties.disabled || this.data.recognizedActualLoading) return;
      const key = keyFor(this.data.recognizedActualQuarterOptions[this.data.recognizedActualQuarterIndex]);
      const result = this.recognizedActualSnapshots[key]; if (!result) return;
      const drafts = { ...(this.data.form.recognized_actual_drafts || {}) }; delete drafts[key];
      this.setData({ 'form.recognized_actual_drafts': drafts }); this.showRecognizedActual(result); this.emit();
    },
    retryRecognizedActual() { if (active(this) && !this.properties.disabled && !this.data.recognizedActualLoading) return this.loadRecognizedActual(true); },
    prepareRecognizedActuals() {
      if (!this.data.recognizedActualVisible || this.properties.visitContext || !this.properties.existing) return [];
      if (!active(this)) throw Error('当前账号已变化，请重新打开商机');
      try {
        const drafts = this.data.form.recognized_actual_drafts || {}, snapshots = this.recognizedActualSnapshots || {};
        for (const [key, draft] of Object.entries(drafts)) if (this.recognizedActualReadErrors[key] && snapshots[key] && hasChanges(draft, snapshots[key])) throw Error(this.recognizedActualReadErrors[key]);
        const entries = payload(drafts, snapshots);
        if (entries.length && this.data.form.materials_dirty) throw Error('物料或交易关系已有修改，确收输入已保留。请先对已修改季度使用“撤销本季度输入”，保存商机后再重新登记确收');
        return entries;
      } catch (error) { this.setData({ recognizedActualError: error.message }); throw error; }
    },
  };
}
module.exports = { data, read, draftFor, hasChanges, payload, sumAmounts, totalText, create };
