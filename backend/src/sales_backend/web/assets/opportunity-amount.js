// The form uses 万元; the API and database use 元. Never guess a unit by size.
export function yuanFromWan(value) {
  const text = String(value ?? '').trim();
  if (!/^\d+(?:\.\d{1,6})?$/.test(text)) throw Error('请按万元填写金额，最多保留六位小数（精确到分）。');
  const [whole, fraction = ''] = text.split('.');
  const fen = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
  if (fen <= 0n || fen >= 1000000000000000000n) throw Error('请填写有效的正数金额。');
  return scaled(fen, 2).replace(/,/g, '');
}

function cents(value) {
  const text = String(value ?? '').trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
}

function grouped(text) { return text.replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
function scaled(value, decimals) {
  const text = String(value).padStart(decimals + 1, '0');
  const whole = text.slice(0, -decimals), fraction = text.slice(-decimals).replace(/0+$/, '');
  return grouped(whole) + (fraction ? '.' + fraction : '');
}

export function amountDisplay(value) {
  const number = cents(value);
  if (number === null) return '金额待填写';
  const exact = scaled(number, 2) + ' 元';
  if (number >= 10000000000n) return exact + '（' + scaled(number, 10) + ' 亿元）';
  if (number >= 1000000n) return exact + '（' + scaled(number, 6) + ' 万元）';
  return exact;
}

export function wanFromYuan(value) {
  const number = cents(value);
  return number === null ? '' : scaled(number, 6).replace(/,/g, '');
}

export function bindAmountPreview(root) {
  const input = root.querySelector('[name=amount_wan]');
  const output = root.querySelector('[data-amount-preview]');
  if (!input || !output) return;
  const render = () => {
    try { output.textContent = '实际金额：' + amountDisplay(yuanFromWan(input.value)); }
    catch (error) { output.textContent = input.value ? error.message : '输入单位为万元，例如填 10 表示 100,000 元（10 万元）。'; }
  };
  input.addEventListener?.('input', render);
  render();
}

// Acknowledgement belongs to this exact submit payload, never a reusable flag.
export async function confirmOpportunityAmount(body, previous, readPolicy, confirm) {
  const current = cents(body.amount);
  if (current === null || current <= 0n) throw Error('请填写有效的商机金额。');
  delete body.amount_confirmed_value;
  if (previous?.id && cents(previous.amount) === current) return;
  const policy = await readPolicy();
  const threshold = policy?.definition?.warning_threshold_wan;
  if (policy?.definition?.schema_version !== 1 || !Number.isSafeInteger(threshold) || threshold < 1) {
    throw Error('金额提醒规则加载失败，请保留输入并重试。');
  }
  if (current < BigInt(threshold) * 1000000n) return;
  const old = previous?.id ? '原金额：' + amountDisplay(previous.amount) + '\n' : '';
  const message = old + '本次金额：' + amountDisplay(body.amount)
    + '\n已达到公司提醒阈值 ' + grouped(String(threshold)) + ' 万元。'
    + '\n请核对是否将“元”误填为“万元”。确认金额正确后将继续保存。';
  if (!confirm(message)) throw Error('尚未确认大额金额，请核对或修改后再保存。');
  body.amount_confirmed_value = body.amount;
}
