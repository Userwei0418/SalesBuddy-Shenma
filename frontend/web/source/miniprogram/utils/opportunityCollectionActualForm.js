const access = require('./access');
const opportunity = require('./opportunity');
const opportunityAmount = require('./opportunityAmount');
const { MESSAGES } = require('./errorMessages');

const keyFor = period => `${period.year}-Q${period.quarter}`;
const text = value => String(value == null ? '' : value).trim();
// Historical read values may retain sub-cent precision or numeric scale. Keep
// them exact; only new input is constrained to cents by opportunity.amount.
const validMoney = value => typeof value === 'string' && /^\d+(\.\d+)?$/.test(value);
const currentText = actual => actual.amount !== null ? `${opportunity.wan(actual.amount)} 万元`
  : ({ missing: '未登记', withdrawn: '已撤回', needs_confirmation: '来源待核对，暂无正式金额',
    stale_confirmation: '原确认已失效', partial: '尚无完整实际金额' }[actual.status] || '暂无正式金额');
const data = {
  collectionActualVisible: false, collectionActualEditable: false,
  collectionActualLoading: false, collectionActualLoaded: false, collectionActualError: '',
  collectionActualQuarterOptions: [], collectionActualQuarterIndex: 0, collectionActualQuarterLabel: '',
  collectionActualAmount: '', collectionActualEvidence: '', collectionActualNote: '', collectionActualDirty: false,
  collectionActualAnyDirty: false, collectionActualAmountPreview: '',
  collectionActualCurrentText: '—', collectionActualReplaceRequired: false, collectionActualReplaceSource: false,
};

function read(result, opportunityId, period) {
  const actual = result && result.actual;
  if (!result || result.opportunity_id !== opportunityId || !result.period ||
      result.period.year !== period.year || result.period.quarter !== period.quarter ||
      typeof result.can_edit !== 'boolean' || !actual || !/^[0-9a-f]{64}$/i.test(actual.revision || '') ||
      !(actual.amount === null || validMoney(actual.amount)) || typeof actual.requires_source_replacement !== 'boolean' ||
      (actual.source_ref != null && typeof actual.source_ref !== 'string') || (actual.note != null && typeof actual.note !== 'string')) {
    throw Error('实际回款信息与当前商机或季度不一致，请重新读取');
  }
  return result;
}

function draftFor(result) {
  const actual = result.actual;
  return { amount: actual.amount === null ? '' : opportunity.wan(actual.amount), evidence: actual.source_ref || '', note: actual.note || '', replaceSource: false };
}

function comparableAmount(value) {
  try { return opportunity.amount(value); } catch (_) { return text(value); }
}

function hasChanges(draft, result) { return comparableAmount(draft.amount) !== comparableAmount(draftFor(result).amount); }

// Only changed cumulative amounts travel with the existing atomic opportunity
// save. An unchanged amount never rewrites finance facts or requires evidence.
function collectionError(code, label, message = MESSAGES[code]) {
  return Object.assign(Error(`${label}：${message}`), { code });
}

function payload(drafts, snapshots, tradeRelation) {
  return Object.entries(drafts || {}).sort(([a], [b]) => a.localeCompare(b)).flatMap(([key, draft]) => {
    const result = snapshots[key];
    if (!result) throw Error('实际回款尚未读取完成，请重新读取后保存');
    if (!hasChanges(draft, result)) return [];
    const period = result.period, label = `${period.year} Q${period.quarter}`;
    if (!result.can_edit) throw collectionError('OPPORTUNITY_COLLECTION_FORBIDDEN', label);
    if (!['direct', 'distributor', 'downstream'].includes(tradeRelation)) throw collectionError('OPPORTUNITY_TRADE_RELATION_REQUIRED', label);
    if (text(draft.amount) === '') throw Error(`${label}：请填写季度累计实际回款，金额为零请填 0`);
    const amount = opportunity.amount(draft.amount), source = text(draft.evidence), note = text(draft.note);
    if (tradeRelation === 'downstream' && Number(amount) !== 0) throw collectionError('OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN', label);
    if (!source || [...source].length > 160) throw Error(`${label}：请填写回款依据，最多 160 字`);
    if ([...note].length > 1000) throw Error(`${label}：备注最多 1000 字`);
    if (result.actual.requires_source_replacement && draft.replaceSource !== true) throw collectionError('OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED', label);
    return [{ year: period.year, quarter: period.quarter, amount: String(amount), source_ref: source, note,
      expected_actual_revision: result.actual.revision,
      ...(result.actual.requires_source_replacement ? { replace_source: true } : {}) }];
  });
}

function create(api, session) {
  const active = component => !component.closed && component.data.collectionActualVisible && component.collectionActualOwner === access.identity(session());
  return {
    resetCollectionActuals() {
      this.collectionActualSequence = (this.collectionActualSequence || 0) + 1;
      this.collectionActualSnapshots = {};
      this.collectionActualReadErrors = {};
      this.collectionActualOwner = access.identity(session());
      const visible = !!(this.properties.existing && this.properties.existing.id && !this.properties.visitContext);
      const options = this.data.quarterOptions || [], now = opportunity.quarterNow();
      const index = Math.max(0, options.findIndex(row => row.year === now.year && row.quarter === now.quarter));
      this.setData({ ...data, collectionActualVisible: visible, collectionActualQuarterOptions: options,
        collectionActualQuarterIndex: index, 'form.collection_actual_drafts': {} });
      if (visible) return this.loadCollectionActual();
    },
    async loadCollectionActual(force = false) {
      if (!active(this)) return;
      const period = this.data.collectionActualQuarterOptions[this.data.collectionActualQuarterIndex];
      if (!period) return;
      const key = keyFor(period), opportunityId = this.properties.existing.id, source = this.sourceKey;
      const sequence = this.collectionActualSequence = (this.collectionActualSequence || 0) + 1;
      const current = () => active(this) && source === this.sourceKey && sequence === this.collectionActualSequence;
      const cached = this.collectionActualSnapshots[key];
      if (cached && !force && !this.collectionActualReadErrors[key]) return this.showCollectionActual(cached);
      this.collectionActualReadErrors[key] = '实际回款正在重新读取，请稍后保存';
      this.setData({ collectionActualLoading: true, collectionActualLoaded: false, collectionActualEditable: false,
        collectionActualQuarterLabel: period.label, collectionActualError: '', collectionActualCurrentText: '—',
        collectionActualAmount: '', collectionActualAmountPreview: '', collectionActualEvidence: '', collectionActualNote: '', collectionActualDirty: false,
        collectionActualReplaceRequired: false, collectionActualReplaceSource: false });
      try {
        const result = read(await api.getOpportunityCollectionActuals(opportunityId, period), opportunityId, period);
        if (!current()) return;
        const previous = this.collectionActualSnapshots[key];
        const drafts = this.data.form.collection_actual_drafts || {};
        // A new source revision needs fresh replacement consent, but typed values
        // survive an explicit refresh after a version conflict or network error.
        if (previous && previous.actual.revision !== result.actual.revision && drafts[key]) {
          this.setData({ 'form.collection_actual_drafts': { ...drafts, [key]: { ...drafts[key], replaceSource: false } } });
        }
        this.collectionActualSnapshots[key] = result;
        delete this.collectionActualReadErrors[key];
        this.showCollectionActual(result);
      } catch (error) {
        if (current()) {
          this.collectionActualReadErrors[key] = error.message || '实际回款读取失败，请重试';
          this.setData({ collectionActualError: this.collectionActualReadErrors[key] });
        }
      } finally { if (current()) this.setData({ collectionActualLoading: false }); }
    },
    showCollectionActual(result) {
      const key = keyFor(result.period), drafts = this.data.form.collection_actual_drafts || {};
      const draft = drafts[key] || draftFor(result), dirty = hasChanges(draft, result);
      this.setData({ collectionActualLoaded: true, collectionActualLoading: false,
        collectionActualEditable: result.can_edit, collectionActualError: '',
        collectionActualQuarterLabel: `${result.period.year} Q${result.period.quarter}`,
        collectionActualAmount: draft.amount, collectionActualEvidence: draft.evidence, collectionActualNote: draft.note,
        collectionActualAmountPreview: opportunityAmount.previewWan(draft.amount),
        collectionActualAnyDirty: Object.entries(drafts).some(([draftKey, value]) =>
          this.collectionActualSnapshots[draftKey] && hasChanges(value, this.collectionActualSnapshots[draftKey])),
        collectionActualDirty: dirty, collectionActualCurrentText: currentText(result.actual),
        collectionActualReplaceRequired: dirty && result.actual.requires_source_replacement,
        collectionActualReplaceSource: draft.replaceSource === true,
      });
    },
    collectionActualQuarter(event) {
      if (!active(this) || this.properties.disabled) return;
      const index = Number(event.detail.value);
      if (!Number.isInteger(index) || !this.data.collectionActualQuarterOptions[index]) return;
      this.setData({ collectionActualQuarterIndex: index });
      return this.loadCollectionActual();
    },
    collectionActualInput(event) {
      if (!active(this) || this.properties.disabled || this.data.collectionActualLoading || !this.data.collectionActualEditable) return;
      const field = event.currentTarget.dataset.key;
      if (!['amount', 'evidence', 'note', 'replaceSource'].includes(field)) return;
      const period = this.data.collectionActualQuarterOptions[this.data.collectionActualQuarterIndex], key = keyFor(period);
      const result = this.collectionActualSnapshots[key];
      if (!result) return;
      const drafts = { ...(this.data.form.collection_actual_drafts || {}) };
      const value = field === 'replaceSource' ? event.detail.value === true : event.detail.value == null ? '' : String(event.detail.value);
      drafts[key] = { ...(drafts[key] || draftFor(result)), [field]: value };
      this.setData({ 'form.collection_actual_drafts': drafts });
      this.showCollectionActual(result);
      this.emit();
    },
    collectionActualReplace(event) {
      const value = event.detail.value;
      return this.collectionActualInput({ currentTarget: { dataset: { key: 'replaceSource' } }, detail: { value: value === true || Array.isArray(value) && value.includes('replace') } });
    },
    retryCollectionActual() {
      if (!active(this) || this.properties.disabled || this.data.collectionActualLoading) return;
      return this.loadCollectionActual(true);
    },
    prepareCollectionActuals() {
      if (!this.data.collectionActualVisible || this.properties.visitContext || !this.properties.existing) return [];
      if (!active(this)) throw Error('当前账号已变化，请重新打开商机');
      try {
        const drafts = this.data.form.collection_actual_drafts || {}, snapshots = this.collectionActualSnapshots || {};
        for (const [key, draft] of Object.entries(drafts)) {
          if (this.collectionActualReadErrors[key] && snapshots[key] && hasChanges(draft, snapshots[key])) throw Error(this.collectionActualReadErrors[key]);
        }
        return payload(drafts, snapshots, this.data.form.trade_relation);
      }
      catch (error) { this.setData({ collectionActualError: error.message }); throw error; }
    },
  };
}

module.exports = { data, create, read, draftFor, hasChanges, payload };
