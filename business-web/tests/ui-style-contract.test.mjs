import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inspectStyles, checkStyles} from '../scripts/check-ui-contract.mjs';

test('new reviewed styles honor shared typography, colors and radii', checkStyles);
test('style check detects new drift and allows structural geometry', () => {
  assert.equal(inspectStyles('a{font-size:13px;color:#abc;border-radius:9px}', 'example').length, 3);
  assert.deepEqual(inspectStyles('a{font-size:var(--ui-text-body);color:var(--ui-ink);border-radius:50%;height:72px;gap:var(--ui-space-3)}', 'example'), []);
});
