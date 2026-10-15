const opportunity = require('./opportunity');

function decimalText(value) {
  const match = String(value == null ? '' : value).trim().match(/^(\d+)(?:\.(\d+))?$/);
  if (!match) return '';
  const whole = match[1].replace(/^0+(?=\d)/, ''), fraction = (match[2] || '').replace(/0+$/, '');
  return whole + (fraction ? `.${fraction}` : '');
}

function grouped(value) {
  const [whole, fraction] = String(value).split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? `.${fraction}` : '');
}

function describeYuan(value) {
  const exact = decimalText(value), yuan = Number(exact);
  if (!exact || !Number.isFinite(yuan) || yuan < 0) return '';
  const wan = opportunity.wan(exact);
  const magnitude = yuan >= 100000000 ? `${grouped(opportunity.wan(wan))} 亿元`
    : `${grouped(wan)} 万元`;
  return `${grouped(exact)} 元（${magnitude}）`;
}

function previewWan(value) {
  try {
    const yuan = opportunity.amount(value);
    return yuan == null ? '' : `折合 ${describeYuan(yuan)}`;
  } catch (_) { return ''; }
}

function cleanMutation(mutation) {
  if (!mutation) return mutation;
  const clean = {...mutation};
  // An acknowledgment belongs only to the current save confirmation, never a draft or AI result.
  delete clean.amount_confirmed_value;
  return clean;
}

function amountChanged(mutation, existing) {
  return mutation.action !== 'update' || !existing || existing.id !== mutation.opportunity_id
    || !decimalText(existing.amount) || decimalText(existing.amount) !== decimalText(mutation.amount);
}

async function assess(api, mutation, existing) {
  const clean = cleanMutation(mutation);
  if (!clean || !amountChanged(clean, existing)) return {mutation:clean, required:false, content:''};
  const amount = Number(clean.amount);
  if (clean.amount == null || !Number.isFinite(amount) || amount <= 0) throw Error('请填写有效的商机金额（万元）');
  if (amount > Number.MAX_SAFE_INTEGER || decimalText(clean.amount) !== decimalText(amount)) throw Error('金额数值过大，无法精确保存，请核对金额单位或联系管理员');
  let threshold;
  try {
    // Always read the current company policy. Do not cache or guess a fallback threshold.
    const presentation = await api.getCompanyPresentation();
    const definition = presentation && presentation.opportunity_amount && presentation.opportunity_amount.definition;
    threshold = definition && definition.warning_threshold_wan;
    if (!definition || definition.schema_version !== 1 || !Number.isSafeInteger(threshold) || threshold <= 0) throw Error('invalid amount policy');
  } catch (_) { throw Error('金额核对规则加载失败，表单已保留，请重试保存'); }
  const required = amount >= threshold * 10000;
  return {mutation:clean, required, content:required
    ? `本次商机金额：${grouped(opportunity.wan(clean.amount))} 万元\n即 ${describeYuan(clean.amount)}\n已达到本公司 ${grouped(threshold)} 万元的提醒值。请确认没有把“元”金额填入“万元”输入框；金额正确可继续保存。` : ''};
}

function acknowledged(assessment) {
  return assessment.required
    ? {...assessment.mutation, amount_confirmed_value:assessment.mutation.amount}
    : assessment.mutation;
}

module.exports = {previewWan, describeYuan, cleanMutation, assess, acknowledged};
