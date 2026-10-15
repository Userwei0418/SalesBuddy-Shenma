import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const compiled = buildSync({absWorkingDir: root, entryPoints: ['department-ui/Bi.jsx'],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  loader: {'.css': 'empty', '.svg': 'dataurl'}}).outputFiles[0].text;
const m = new Module(root + 'bi-ranking-test.cjs');
m.filename = root + 'bi-ranking-test.cjs'; m.paths = Module._nodeModulePaths(root);
m._compile(compiled, m.filename);
const data = {
  viewMode: 'team', teamLabel: '南区', selectedQuarter: {label: '2026 Q4'},
  quarterEmpty: true, kpis: [{label: '实际不含税确收', value: '¥140', sub: '已登记小计'}],
  rankingCards: [{key: 'acv', title: '团队商机 ACV 排名', subtitle: '南区', period: '2026 Q4',
    summaryIds: ['stale'], rows: [{id: 'stale', name: '旧排名对象', rank: 1, value: 100, displayValue: '100元'}]}],
};
const render = extra => renderToStaticMarkup(React.createElement(m.exports.default, {
  page: {}, data: {...data, ...extra}, invoke() {throw Error('Rendering must not request data');},
}));

test('ranking failure explains the business issue without a false network claim or stale rows', () => {
  const html = render({rankingMessage: '此团队目录用途不支持指定权限'});
  assert.match(html, /当前团队排名暂时无法加载，请稍后重试。/);
  assert.match(html, /排名加载失败/); assert.match(html, /重试/);
  assert.match(html, /实际不含税确收/); assert.match(html, /140/);
  assert.doesNotMatch(html, /网络不通|旧排名对象|此团队目录用途不支持指定权限/);
});

test('permission guidance remains actionable and internal diagnostics are not disclosed', () => {
  assert.match(render({rankingMessage: '当前账号未获查看排名授权'}), /没有查看排名的权限，请联系运营/);
  const html = render({rankingMessage: 'Traceback secret-diagnostic SELECT internal_table'});
  assert.match(html, /排名暂时无法加载，请稍后重试。/);
  assert.doesNotMatch(html, /Traceback|secret-diagnostic|internal_table|旧排名对象/);
});

test('retry loading hides the previous failure and rows; successful data becomes visible', () => {
  const pending = render({rankingLoading: true, rankingMessage: '当前账号未获查看排名授权'});
  assert.match(pending, /正在加载/); assert.doesNotMatch(pending, /排名加载失败|旧排名对象|联系运营/);
  const success = render({rankingLoading: false, rankingMessage: ''});
  assert.match(success, /旧排名对象/); assert.doesNotMatch(success, /排名加载失败|正在加载/);
});
