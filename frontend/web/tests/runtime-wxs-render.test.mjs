import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const code = fs.readFileSync(root + 'runtime.js', 'utf8');
// Run the actual WXML parser class without importing/executing the build script.
const wxs = JSON.parse(execFileSync('python3', ['-c', `
import ast, json
from pathlib import Path
from html.parser import HTMLParser
root=Path(${JSON.stringify(root)})
source=ast.parse((root/'scripts/build.py').read_text())
cls=next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name=='WXML')
exec(compile(ast.Module(body=[cls],type_ignores=[]),'WXML','exec'))
parser=WXML();parser.feed((root/'source/miniprogram/components/opportunity-form/index.wxml').read_text())
print(json.dumps([node for node in parser.root['c'] if isinstance(node,dict) and node['t']=='wxs']))
`], {encoding: 'utf8'}));

function render(nodes, component = false) {
  const record = {config: {}, templates: {normal: [{t: 'view', a: {}, c: ['正常模板']}]}};
  const owner = component ? {_definition: {}, _module: 'fixture'} : {route: 'fixture'};
  const context = vm.createContext({bundle: {pages: {fixture: record}, components: {fixture: record}, config: {}},
    expressionCache: new Map(), camel: s => s, cssRpx: s => s});
  vm.runInContext(code.slice(code.indexOf('  function evaluate('), code.indexOf('  function call(')) +
    code.slice(code.indexOf('  function textNode('), code.indexOf('  function patchChildren(')) + '\nglobalThis.render = children;', context);
  return JSON.parse(JSON.stringify(context.render(nodes, {}, owner, 'fixture')));
}
const content = nodes => nodes.map(n => n.text ?? content(n.children || [])).join('');

test('real inline opportunity WXS never renders while a page or component is loading', () => {
  assert.equal(wxs.length, 2);
  assert.match(JSON.stringify(wxs), /function lab/);
  for (const component of [false, true]) {
    const result = render([...wxs, {t: 'view', a: {}, c: ['加载中…']}], component);
    assert.equal(content(result), '加载中…');
    assert.equal(result.length, 1);
    assert.doesNotMatch(JSON.stringify(result), /module.exports|function lab|"tag":"wxs"/);
  }
});

test('external WXS is nonvisual while ordinary content and named templates still render', () => {
  const result = render([{t: 'wxs', a: {src: './helpers.wxs', module: 'ui'}, c: []},
    {t: 'import', a: {src: './parts.wxml'}, c: []},
    {t: 'template', a: {name: 'normal'}, c: ['声明不展示']},
    {t: 'template', a: {is: 'normal'}, c: []},
    {t: 'view', a: {}, c: ['用户正文中 module.exports 作为普通文本保留']}]);
  assert.equal(result.length, 2);
  assert.equal(content(result), '正常模板用户正文中 module.exports 作为普通文本保留');
});
