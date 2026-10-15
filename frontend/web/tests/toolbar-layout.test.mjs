import test from 'node:test';
import assert from 'node:assert/strict';
import {visibleControlCount} from '../department-ui/pc/toolbar-layout.js';

test('toolbar keeps all filters visible when they fit, without reserving an unused trigger', () => {
  assert.equal(visibleControlCount(1100, Array(6).fill(144), 180), 6);
});
test('toolbar reserves space for search, reset and overflow instead of wrapping the last filter', () => {
  const widths = Array(8).fill(144);
  assert.equal(visibleControlCount(1120, widths, 180, 72), 4);
  assert.equal(visibleControlCount(760, widths, 180, 72), 2);
  assert.equal(visibleControlCount(360, widths, 120, 72), 0);
});
test('toolbar accounts for wider period and scope controls and restores them when expanded', () => {
  assert.equal(visibleControlCount(700, [280, 340, 104]), 1);
  assert.equal(visibleControlCount(900, [280, 340, 104]), 3);
  assert.equal(visibleControlCount(100, []), 0);
});
