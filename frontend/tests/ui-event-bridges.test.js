const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../miniprogram');
const clone = value => JSON.parse(JSON.stringify(value));

function load(relative, {props = {}, wx = {}, app, requireOverride} = {}) {
  const file = path.join(root, relative);
  let definition;
  const context = {
    Page: value => { definition = value; }, Component: value => { definition = value; },
    require: name => requireOverride && requireOverride(name) || require(path.resolve(path.dirname(file), name)),
    getApp: () => app || {globalData: {session: {workspaceId: 'w', userId: 'u', role: 'sales'}}},
    wx, Date, setTimeout, clearTimeout, console,
  };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context, {filename: file});
  assert.ok(definition, relative);
  const defaults = {};
  for (const [key, value] of Object.entries(definition.properties || {})) {
    defaults[key] = value && Object.hasOwn(value, 'value') ? clone(value.value) : undefined;
  }
  const properties = {...defaults, ...props};
  const emitted = [];
  const instance = {
    ...definition, ...definition.methods, properties,
    data: {...properties, ...clone(definition.data || {})},
    setData(values, callback) {
      for (const [key, value] of Object.entries(values)) {
        const parts = key.split('.');
        let target = this.data;
        while (parts.length > 1) target = target[parts.shift()];
        target[parts[0]] = value;
      }
      if (callback) callback.call(this);
    },
    triggerEvent(type, detail = {}) { emitted.push({type, detail}); },
  };
  return {instance, definition, emitted};
}

// Deliver the custom component's real detail payload with the host WXML dataset.
function connect(child, event, parent, handler, dataset = {}) {
  child.triggerEvent = (type, detail = {}) => {
    if (type === event) return parent[handler]({detail, currentTarget: {dataset}});
  };
}

function loadCheckbox(group = false) {
  const name = group ? 'checkbox-group' : 'checkbox';
  const file = path.join(root, 'ui/tdesign', name, `${name}.js`);
  const module = {exports: {}};
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    module, exports: module.exports,
    require: name => name === '../common/src/index'
      ? {SuperComponent: class {}, wxComponent: () => value => value}
      : require(path.resolve(path.dirname(file), name)),
  }, {filename: file});
  const instance = new module.exports.default();
  Object.assign(instance, instance.methods);
  instance.setData = values => Object.assign(instance.data, values);
  return instance;
}

test('all page and business component WXML event handlers resolve after UI migration', () => {
  let bindingCount = 0;
  for (const folder of ['pages', 'components']) {
    for (const item of fs.readdirSync(path.join(root, folder))) {
      const file = path.join(root, folder, item, 'index.wxml');
      if (!fs.existsSync(file)) continue;
      const {instance} = load(`${folder}/${item}/index.js`);
      const text = fs.readFileSync(file, 'utf8');
      for (const match of text.matchAll(/\b(?:capture-)?(?:bind|catch):?[\w-]+\s*=\s*["']([A-Za-z_$][\w$]*)["']/g)) {
        assert.equal(typeof instance[match[1]], 'function', `${folder}/${item}: ${match[1]}`);
        bindingCount++;
      }
    }
  }
  assert.ok(bindingCount > 400, `checked ${bindingCount} bindings`);
});

test('segmented task scope still obeys team permission and resets member filter only on accepted switch', () => {
  const page = load('pages/tasks/index.js').instance;
  const control = load('ui/sb/sb-segmented/index.js', {props: {value: 'self'}}).instance;
  connect(control, 'change', page, 'onTaskViewChange');
  let reads = 0;
  page.loadTasks = () => { reads++; };
  Object.assign(page.data, {taskView: 'self', canViewTeam: false, fdeTaskMemberId: 'member'});
  control.onTap({currentTarget: {dataset: {value: 'team'}}});
  assert.equal(page.data.taskView, 'self');
  assert.equal(page.data.fdeTaskMemberId, 'member');
  assert.equal(reads, 0);
  page.data.canViewTeam = true;
  control.onTap({currentTarget: {dataset: {value: 'team'}}});
  assert.equal(page.data.taskView, 'team');
  assert.equal(page.data.fdeTaskMemberId, '');
  assert.equal(reads, 1);
  control.data.disabled = true;
  control.onTap({currentTarget: {dataset: {value: 'self'}}});
  assert.equal(reads, 1);
});

test('tabs send key rather than value; same, disabled and unknown task tabs do not load', () => {
  const page = load('pages/tasks/index.js').instance;
  const tabs = load('ui/sb/sb-tabs/index.js', {props: {activeKey: 'pending'}}).instance;
  connect(tabs, 'change', page, 'onTabChange');
  let reads = 0;
  page.loadTasks = () => { reads++; };
  for (const dataset of [{key: 'pending'}, {key: 'completed', disabled: true}, {key: 'unknown'}]) {
    tabs.onTap({currentTarget: {dataset}});
  }
  assert.equal(reads, 0);
  tabs.onTap({currentTarget: {dataset: {key: 'completed'}}});
  assert.equal(page.data.activeTab, 'completed');
  assert.equal(reads, 1);
});

test('select numeric index reaches owner-team field and disabled choice cannot change it', () => {
  const form = load('components/opportunity-form/index.js').instance;
  form.data.ownerTeams = [{id: 'team-a', name: 'A'}, {id: 'team-b', name: 'B'}];
  const select = load('ui/sb/sb-select/index.js', {props: {options: [{value: 0, label: 'A'}, {value: 1, label: 'B'}], value: 0}, wx: {showToast() {}}}).instance;
  connect(select, 'change', form, 'changeOwnerTeam');
  select.onConfirm({detail: {value: [1]}});
  assert.equal(form.data.form.owner_team_id, 'team-b');
  assert.equal(form.data.ownerTeamIndex, 1);
  form.properties.disabled = true;
  select.data.value = 1;
  select.onConfirm({detail: {value: [0]}});
  assert.equal(form.data.form.owner_team_id, 'team-b');
  form.properties.disabled = false;
  select.data.options[0].disabled = true;
  select.onConfirm({detail: {value: [0]}});
  assert.equal(form.data.form.owner_team_id, 'team-b');
});

test('input change preserves host dataset field and page disabled guard', () => {
  const form = load('components/opportunity-form/index.js').instance;
  const input = load('ui/sb/sb-input/index.js').instance;
  connect(input, 'change', form, 'input', {key: 'product_line'});
  input.onChange({detail: {value: '演示产品线'}});
  assert.equal(form.data.form.product_line, '演示产品线');
  form.properties.disabled = true;
  input.onClear();
  assert.equal(form.data.form.product_line, '演示产品线');
  form.properties.disabled = false;
  input.onClear();
  assert.equal(form.data.form.product_line, '');
});

test('datetime and priority bridges preserve independent recipients and submit lock', () => {
  const page = load('pages/management-task-create/index.js').instance;
  const date = load('ui/sb/sb-date-picker/index.js', {props: {mode: 'datetime'}}).instance;
  const priority = load('ui/sb/sb-segmented/index.js').instance;
  connect(date, 'change', page, 'onDueChange');
  connect(priority, 'change', page, 'onPriorityChange');
  page.data.selectedMembers = [{id: 'a'}, {id: 'b'}];
  date.onConfirm({detail: {value: '2026-10-03 16:45'}});
  assert.equal(page.data.customDueDate, '2026-10-03');
  assert.equal(page.data.customDueTime, '16:45');
  assert.deepEqual(page.data.selectedMembers.map(x => x.id), ['a', 'b']);
  priority.onTap({currentTarget: {dataset: {value: '高'}}});
  assert.equal(page.data.selectedPriority, '高');
  page.data.submissionPending = true;
  date.onConfirm({detail: {value: '2026-10-04 09:00'}});
  priority.onTap({currentTarget: {dataset: {value: '普通'}}});
  assert.equal(page.data.customDueDate, '2026-10-03');
  assert.equal(page.data.customDueTime, '16:45');
  assert.equal(page.data.selectedPriority, '高');
});

test('sheet can close partner picker after form becomes disabled; hidden/locked confirm cannot submit', () => {
  const form = load('components/opportunity-form/index.js', {props: {disabled: true}}).instance;
  form.data.showPartnerSearch = true;
  const {instance: sheet, emitted} = load('ui/sb/sb-sheet/index.js', {props: {visible: true}});
  const originalTrigger = sheet.triggerEvent.bind(sheet);
  sheet.triggerEvent = (type, detail) => type === 'close' ? form.closePartners({detail}) : originalTrigger(type, detail);
  sheet.onVisible({detail: {visible: false, trigger: 'overlay'}});
  assert.equal(form.data.showPartnerSearch, false);
  sheet.data.confirmDisabled = true;
  sheet.onConfirm();
  sheet.data.confirmDisabled = false;
  sheet.data.visible = false;
  sheet.onConfirm();
  assert.equal(emitted.length, 0);
});

test('password eye visibility is idempotent and controlled reset is respected', () => {
  const page = load('pages/login/index.js').instance;
  const {instance: input, definition} = load('ui/sb/sb-input/index.js');
  connect(input, 'visibility', page, 'onPasswordVisibility');
  page.data.passwordVisible = false;
  input.onEye();
  assert.equal(page.data.passwordVisible, true);
  page.onPasswordVisibility({detail: {visible: true}});
  assert.equal(page.data.passwordVisible, true);
  page.data.passwordVisible = false;
  definition.observers.showPassword.call(input, false);
  assert.equal(input.data.showPlain, false);
  input.onEye();
  assert.equal(page.data.passwordVisible, true);
});

test('summary metric item routes only supported task counters', () => {
  const urls = [];
  const page = load('pages/index/index.js', {wx: {navigateTo: ({url}) => urls.push(url)}}).instance;
  const card = load('ui/sb/sb-summary-card/index.js', {props: {metricsTappable: true, metrics: [{key: 'today_pending'}, {key: 'unsupported'}]}}).instance;
  connect(card, 'metrictap', page, 'onOverviewMetricTap');
  card.onMetric({currentTarget: {dataset: {index: 0}}});
  card.onMetric({currentTarget: {dataset: {index: 1}}});
  assert.deepEqual(urls, ['/pages/tasks/index?overview=today_pending']);
});

test('description key/item opens only a linked opportunity and does not invent a relation', () => {
  const writes = [], urls = [];
  const page = load('pages/task-detail/index.js', {wx: {setStorageSync: (...args) => writes.push(args), switchTab: ({url}) => urls.push(url)}}).instance;
  const {instance: desc, definition} = load('ui/sb/sb-desc-list/index.js', {props: {items: [{key: 'opportunity', value: '演示商机', tappable: true}]}});
  definition.observers.items.call(desc, desc.data.items);
  connect(desc, 'tap', page, 'onInfoTap');
  page.data.task = {customer_id: 'c', opportunity_id: ''};
  desc.onTap({currentTarget: {dataset: {index: 0}}});
  assert.equal(urls.length, 0);
  page.data.task.opportunity_id = 'o';
  desc.onTap({currentTarget: {dataset: {index: 0}}});
  assert.deepEqual(writes, [['pendingOpenCustomerId', 'c'], ['pendingOpenOpportunityId', 'o']]);
  assert.deepEqual(urls, ['/pages/customers/index']);
});

test('entry-mode segmented retains recorder busy gate and only valid modes persist', () => {
  const page = load('pages/visit-entry/index.js').instance;
  const segmented = load('ui/sb/sb-segmented/index.js', {props: {value: 'voice'}}).instance;
  connect(segmented, 'change', page, 'onEntryModeChange');
  let saves = 0;
  page.persist = () => { saves++; };
  page.data.isRecording = true;
  segmented.onTap({currentTarget: {dataset: {value: 'file'}}});
  assert.equal(page.data.entryMode, 'voice');
  page.data.isRecording = false;
  segmented.onTap({currentTarget: {dataset: {value: 'file'}}});
  assert.equal(page.data.entryMode, 'file');
  segmented.onTap({currentTarget: {dataset: {value: 'unknown'}}});
  assert.equal(saves, 1);
});

test('labeled picker clear maps null back to all without losing the host filter key', () => {
  const page = load('pages/opportunities/index.js').instance;
  const select = load('ui/sb/sb-labeled-select/index.js', {props: {options: [{value: 2, label: '高'}]}}).instance;
  connect(select, 'change', page, 'changeFilter', {key: 'grade'});
  let reads = 0;
  page.applyFilters = () => { reads++; };
  select.onConfirm({detail: {value: [2]}});
  assert.equal(page.data.gradeIndex, 2);
  select.onClear();
  assert.equal(page.data.gradeIndex, 0);
  assert.equal(reads, 2);
});

test('TDesign checkbox checked event keeps recipient dataset and independent multi-selection', () => {
  const page = load('pages/management-task-create/index.js').instance;
  page.data.members = [{id: 'a', name: '甲'}, {id: 'b', name: '乙'}];
  page.data.selectedMembers = [];
  const box = loadCheckbox();
  Object.assign(box.data, {checked: false, _disabled: false});
  let target = 'a';
  box._trigger = (type, detail) => {
    assert.equal(type, 'change');
    assert.equal(typeof detail.checked, 'boolean');
    page.toggleRecipient({detail, currentTarget: {dataset: {id: target}}});
  };
  box.handleTap({currentTarget: {dataset: {target: 'label'}}});
  target = 'b';
  box.handleTap({currentTarget: {dataset: {target: 'label'}}});
  assert.deepEqual(page.data.selectedMembers.map(x => x.id), ['a', 'b']);
  page.data.submissionPending = true;
  box.handleTap({currentTarget: {dataset: {target: 'label'}}});
  assert.deepEqual(page.data.selectedMembers.map(x => x.id), ['a', 'b']);
  page.data.submissionPending = false;
  box.data._disabled = true;
  box.handleTap({currentTarget: {dataset: {target: 'label'}}});
  assert.deepEqual(page.data.selectedMembers.map(x => x.id), ['a', 'b']);
});

test('TDesign checkbox group preserves selected collaborators hidden by search', () => {
  const page = load('pages/visit-confirm/index.js').instance;
  Object.assign(page.data, {isFde: false, colleagues: [{id: 'visible'}], collaboratorIds: ['hidden']});
  page.refresh = () => {};
  page.persist = () => {};
  const group = loadCheckbox(true);
  group.data.value = ['hidden'];
  group.getChildren = () => [{data: {value: 'visible'}}];
  group._trigger = (type, detail) => page.selectColleagues({detail});
  group.updateValue({value: 'visible', checked: true});
  assert.deepEqual(Array.from(page.data.collaboratorIds), ['hidden', 'visible']);
  group.data.value = ['hidden', 'visible'];
  group.updateValue({value: 'visible', checked: false});
  assert.deepEqual(Array.from(page.data.collaboratorIds), ['hidden']);
});
