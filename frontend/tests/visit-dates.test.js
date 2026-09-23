const test = require('node:test');
const assert = require('node:assert/strict');
const dates = require('../miniprogram/utils/visitDates');
test('北京时间跨日、历史无时区时间和跨年七日边界', () => {
  assert.equal(dates.dateValue('2026-09-09T16:00:00Z'), '2026-09-10');
  assert.equal(dates.dateValue('2026-09-09 23:30'), '2026-09-09');
  assert.equal(dates.dateLabel('2025-12-31'), '2025年12月31日');
  assert.equal(dates.withinSevenDays('2025-12-27', '2026-01-02'), true);
  assert.equal(dates.withinSevenDays('2025-12-26', '2026-01-02'), false);
  assert.equal(dates.withinSevenDays('2026-01-03', '2026-01-02'), false);
  assert.equal(dates.withinSevenDays('', '2026-01-02'), null);
});
