const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {normalizeCustomerDetail} = require('../miniprogram/utils/customerDetail');

const labels = ['客户潜力', '关系深度', '商机成熟', '拜访活跃', '决策链', '风险健康'];
function detail(values, extra = {}) {
  return normalizeCustomerDetail({
    id: 'customer', name: '客户',
    profile: {dimensions: labels.map((label, index) => ({label, value: values[index]}))},
    ...extra,
  });
}

test('五维已有数据仍绘制真实点线，风险未知保持空缺且不闭合', () => {
  const customer = detail([65, 80, 50, 100, 30, null]);
  assert.equal(customer.profileComplete, false);
  assert.equal(customer.customerProfile[5].value, '—');
  assert.match(customer.profileHint, /5\/6/);
  assert.deepEqual(customer.customerRadar.points.map(point => point.index), [0, 1, 2, 3, 4]);
  assert.deepEqual(customer.customerRadar.segments.map(line => [line.from, line.to]), [[0, 1], [1, 2], [2, 3], [3, 4]]);
  assert.equal(customer.customerRadar.fillStyle, '');
  const radii = customer.customerRadar.points.map(point => Math.hypot(point.x - 50, point.y - 50) * 2);
  radii.forEach((radius, index) => assert.ok(Math.abs(radius - [65, 80, 50, 100, 30][index]) < 1e-9));
});

test('完整六维闭合并填充，100分与六边形轴端重合', () => {
  const customer = detail([100, 100, 100, 100, 100, 100]);
  assert.equal(customer.profileComplete, true);
  assert.equal(customer.customerRadar.points.length, 6);
  assert.equal(customer.customerRadar.segments.length, 6);
  assert.deepEqual(customer.customerRadar.segments[5].from, 5);
  assert.deepEqual(customer.customerRadar.segments[5].to, 0);
  const top = customer.customerRadar.points[0], bottom = customer.customerRadar.points[3];
  assert.ok(Math.abs(top.x - 50) < 1e-9 && top.y === 0);
  assert.ok(Math.abs(bottom.x - 50) < 1e-9 && bottom.y === 100);
  assert.match(customer.customerRadar.fillStyle, /polygon\(50\.00% 0\.00%,93\.30% 25\.00%/);
});

test('任意缺失组合都不跨空缺连线，首尾已知维度仍可相连', () => {
  for (let mask = 0; mask < 64; mask++) {
    const values = labels.map((_, index) => mask & (1 << index) ? 50 : null);
    const radar = detail(values).customerRadar;
    assert.equal(radar.points.length, values.filter(value => value !== null).length);
    for (const line of radar.segments) {
      assert.notEqual(values[line.from], null);
      assert.notEqual(values[line.to], null);
      assert.equal((line.to - line.from + 6) % 6, 1);
    }
    assert.equal(radar.segments.some(line => line.from === 5 && line.to === 0), values[5] !== null && values[0] !== null);
    assert.equal(Boolean(radar.fillStyle), mask === 63);
  }
});

test('全空和加载中的画像不出现伪造面或原点', () => {
  for (const customer of [detail([]), detail([100, 100, 100, 100, 100, 100], {read_model: 'detail_header_v1'})]) {
    assert.equal(customer.profileComplete, false);
    assert.deepEqual(customer.customerProfile.map(item => item.value), ['—', '—', '—', '—', '—', '—']);
    assert.equal(customer.customerRadar.points.length, 0);
    assert.equal(customer.customerRadar.segments.length, 0);
    assert.equal(customer.customerRadar.fillStyle, '');
  }
});

test('真实零分保留在圆心，非法值不生成NaN样式', () => {
  const customer = detail([0, null, NaN, Infinity, '', undefined]);
  assert.match(customer.profileHint, /1\/6/);
  assert.equal(customer.customerProfile[0].value, 0);
  assert.equal(customer.customerRadar.points.length, 1);
  assert.equal(customer.customerRadar.points[0].x, 50);
  assert.equal(customer.customerRadar.points[0].y, 50);
  assert.equal(customer.customerRadar.segments.length, 0);
  assert.doesNotMatch(JSON.stringify(customer.customerRadar), /NaN|Infinity/);
});

test('地图点线不受完整度开关隐藏，并保留空态与缺项说明', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  assert.match(wxml, /<view class="radar-series">\s*<view wx:if="\{\{selectedBattleCustomer.profileComplete\}\}"[^>]*><\/view>\s*<view wx:for="\{\{selectedBattleCustomer.customerRadar.segments\}\}"/);
  assert.match(wxml, /wx:for="\{\{selectedBattleCustomer.customerRadar.points\}\}"/);
  assert.match(wxml, /radar-label-missing/);
  assert.match(wxml, /暂无画像数据/);
  assert.match(wxml, /蓝色为已有数据，缺项处断开/);
});
