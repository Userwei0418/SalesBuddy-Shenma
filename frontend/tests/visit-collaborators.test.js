const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const snapshot = require('../miniprogram/utils/visitSnapshot');

function makePage() {
  const file = path.resolve(__dirname, '../miniprogram/pages/visit-confirm/index.js');
  let page;
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value; },
    require: name => name.endsWith('/apiClient') ? {} : require(path.resolve(path.dirname(file), name)),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = value => Object.assign(page.data, value);
  page.persist = () => {};
  return page;
}

test('an unloaded/failed directory does not erase restored collaborator selections', () => {
  const page = makePage();
  page.data.collaboratorIds = ['sales', 'fde'];
  page.refresh();
  assert.deepEqual(page.data.collaboratorIds, ['sales', 'fde']);
});

test('current eligibility removes hidden stale selections with a notice and invalidates quality', () => {
  const page = makePage();
  page.data.collaboratorIds = ['sales', 'fde', 'leader'];
  page.data.reviewedContent = snapshot.signature(page.data);
  page.allColleagues = [{id: 'sales', name: '销售'}, {id: 'leader', name: '老板'}];
  page.refresh();
  assert.deepEqual(page.data.collaboratorIds, ['sales', 'leader']);
  assert.equal(page.data.collaboratorNames, '销售、老板');
  assert.match(page.data.errorText, /已移除.*FDE/);
  assert.equal(page.data.reviewStale, true);
  assert.equal(page.data.canSubmit, false);
});

test('search and multiselect retain eligible colleagues outside the current search', () => {
  const page = makePage();
  page.allColleagues = [{id: 'sales', name: '销售'}, {id: 'admin', name: '运营'}, {id: 'boss', name: '老板'}];
  page.data.collaboratorIds = ['sales', 'boss'];
  page.inputColleagueQuery({detail: {value: '运营'}});
  page.selectColleagues({detail: {value: ['admin']}});
  assert.equal(page.data.collaboratorIds.join(','), 'sales,boss,admin');
});
