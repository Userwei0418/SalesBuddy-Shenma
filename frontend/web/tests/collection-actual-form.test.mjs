import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
const source = transformSync(fs.readFileSync(new URL('../department-ui/OpportunityCreate.jsx', import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code;
const module = {exports: {}};
const Input = Object.assign(() => null, {TextArea: () => null});
vm.runInNewContext(source, {module, exports: module.exports,
  require: name => name === 'react' ? React : name === './ImeInput.jsx' ? {Input, SbSearch: 'SbSearch'} : new Proxy({}, {get: (_, key) => key})});
const {QuarterCollectionActual} = module.exports;
const all = (node, predicate) => !React.isValidElement(node) ? [] : [...(predicate(node) ? [node] : []), ...React.Children.toArray(node.props.children).flatMap(child => all(child, predicate))];
const text = node => typeof node === 'string' ? node : React.isValidElement(node) ? React.Children.toArray(node.props.children).map(text).join('') : '';
function render(overrides = {}, disabled = false) {
  const calls = [], form = {properties: {existing: {id: 'opportunity'}}, data: {
    collectionActualVisible: true, collectionActualEditable: true, collectionActualLoaded: true, collectionActualAmount: '15.123456', collectionActualEvidence: '单号',
    collectionActualCurrentText: '10 万元',
    collectionActualQuarterOptions: [{label: '2026 Q4'}], collectionActualQuarterIndex: 0, ...overrides}};
  const tree = QuarterCollectionActual({form, disabled, invokeOn: (...args) => calls.push(args)});
  return {form, tree, calls};
}

test('quarter collection shows current actual and preserves decimal strings through shared handlers', () => {
  const view = render();
  assert.match(text(view.tree), /当前实际回款10 万元/);
  assert.match(text(view.tree), /保存后直接更新/);
  assert.doesNotMatch(text(view.tree), /待确认|退回|运营确认/);
  const amount = all(view.tree, node => node.props['aria-label'] === '本季度累计回款（万元）')[0];
  assert.equal(amount.props.value, '15.123456');
  amount.props.onChange({target: {value: '15.000001'}});
  assert.equal(view.calls[0][1], 'collectionActualInput');
  assert.equal(view.calls[0][2].detail.value, '15.000001');
  assert.equal(all(view.tree, node => node.props.type === 'submit').length, 0, 'reuse the existing opportunity save');
  const replacement = render({collectionActualReplaceRequired: true, collectionActualReplaceSource: false});
  const choice = all(replacement.tree, node => node.type === 'Checkbox')[0];
  assert.equal(choice.props.checked, false);
  choice.props.onChange({target: {checked: true}});
  assert.equal(replacement.calls[0][1], 'collectionActualReplace');
  assert.equal(replacement.calls[0][2].detail.value, true);
});

test('loading and permission denial block collection inputs; creation and visit contexts have no collection form', () => {
  for (const flags of [{collectionActualLoading: true}, {collectionActualEditable: false}]) {
    const view = render(flags), amount = all(view.tree, node => node.props['aria-label'] === '本季度累计回款（万元）')[0];
    assert.ok(amount.props.disabled); amount.props.onChange({target: {value: '20'}}); assert.equal(view.calls.length, 0);
  }
  const view = render();
  delete view.form.properties.existing;
  assert.equal(QuarterCollectionActual({form: view.form}), null);
  view.form.properties = {existing: {}, visitContext: true};
  assert.equal(QuarterCollectionActual({form: view.form}), null);
  assert.equal(all(view.tree, node => node.props['aria-label'] === '本季度累计回款（万元）')[0].props.disabled, false);
  const failed = render({collectionActualLoaded: false, collectionActualError: '读取失败'});
  assert.equal(all(failed.tree, node => node.props['aria-label'] === '本季度累计回款（万元）').length, 0);
  assert.match(text(failed.tree), /读取失败/); assert.equal(all(failed.tree, node => node.props.className === 'ds-oc-collection-summary').length, 0);
});

test('actual collection amount explains the existing 万元 conversion next to its input', () => {
  const view = render({collectionActualAmount: '10000', collectionActualAmountPreview: '折合 100,000,000 元（1 亿元）'});
  const field = all(view.tree, node => node.type === 'SbField' && node.props.label === '本季度累计回款（万元）')[0];
  assert.match(field.props.help, /100,000,000 元（1 亿元）/);
});

test('trade relationship becomes required for changed collection drafts, without making legacy unchanged records required', () => {
  const materialModule = {exports: {}};
  const code = transformSync(fs.readFileSync(new URL('../department-ui/OpportunityMaterials.jsx', import.meta.url), 'utf8'), {loader:'jsx',format:'cjs'}).code;
  vm.runInNewContext(code, {module:materialModule,exports:materialModule.exports,
    require:name=>name==='react'?React:new Proxy({}, {get:(_,key)=>key})});
  for (const dirty of [false,true]) {
    const tree = materialModule.exports.default({form:{data:{form:{material_lines:[]},collectionActualAnyDirty:dirty}},invokeOn:()=>{}});
    const field = all(tree, node=>node.type==='SbField' && node.props.label==='交易关系')[0];
    assert.equal(field.props.required,dirty);
    if (dirty) assert.match(field.props.help,/季度实际回款前，请选择交易关系/);
  }
});
