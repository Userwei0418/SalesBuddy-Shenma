const CUSTOMER_LEVELS = require('./businessOptions').customer.level_code;

function normalizeCustomerLevel(value) {
  const text = String(value || '').trim();
  const matched = text.match(/^tier[\s_-]?([123])$/i);
  if (matched) return `Tier-${matched[1]}`;
  const legacy = text.match(/^([abc])[级]?[客户]?$/i);
  if (legacy) return `Tier-${{ A: 1, B: 2, C: 3 }[legacy[1].toUpperCase()]}`;
  return '';
}

module.exports = { CUSTOMER_LEVELS, normalizeCustomerLevel };
