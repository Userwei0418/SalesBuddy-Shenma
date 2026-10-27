import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {writeFile,unlink,readFile} from 'node:fs/promises';
const root=fileURLToPath(new URL('../',import.meta.url));
const result=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';import Knowledge from './department-ui/Knowledge.jsx';import KnowledgeUnavailable from './department-ui/knowledge/KnowledgeUnavailable.jsx';import {DonutChart,TrendChart,ProductChart} from './department-ui/knowledge/KnowledgeCharts.jsx';export const unavailable=(tab,service)=>renderToStaticMarkup(<Knowledge page={{options:{tab}}} service={service} distributionService={service}/>);export const publicUnavailable=()=>renderToStaticMarkup(<KnowledgeUnavailable/>);export const donut=rows=>renderToStaticMarkup(<DonutChart rows={rows} title="类别"/>);export const trend=rows=>renderToStaticMarkup(<TrendChart rows={rows}/>);export const products=rows=>renderToStaticMarkup(<ProductChart rows={rows} onSelect={()=>{}}/>);`},loader:{'.css':'empty'},bundle:true,platform:'node',format:'esm',write:false,packages:'external'});
const path=root+'.knowledge-chart-test.mjs';await writeFile(path,result.outputFiles[0].text);const charts=await import(path);await unlink(path);
test('category shares use returned counts and empty data never creates demo values',()=>{const html=charts.donut([{code:'a',label:'甲',count:3},{code:'b',label:'乙',count:1}]);assert.match(html,/共 4 份/);assert.match(html,/75%/);assert.match(html,/25%/);assert.doesNotMatch(charts.donut([]),/<circle/);});
test('trend shows the external series only when every value is available',()=>{const rows=[{date:'2026-10-19',count:4,external_count:2},{date:'2026-10-20',count:5,external_count:3}];assert.equal((charts.trend(rows).match(/<polyline/g)||[]).length,2);delete rows[0].external_count;assert.equal((charts.trend(rows).match(/<polyline/g)||[]).length,1);});
test('product order and accessible counts follow the server directory',()=>{const html=charts.products([{code:'z',label:'产品先',count:2},{code:'a',label:'产品后',count:9}]);assert.ok(html.indexOf('产品先')<html.indexOf('产品后'));assert.match(html,/产品后 9 份/);});

test('all knowledge tabs and legacy aliases render only unavailable without reading a service', async () => {
  const forbidden = new Proxy({}, {get: () => {throw Error('No knowledge request is allowed');}});
  for (const tab of ['dashboard','materials','assistant','works','catalog','playbook','proposal']) {
    const html = charts.unavailable(tab, forbidden);
    assert.match(html, /暂不能使用/);
    assert.doesNotMatch(html, /<button|<input|<form|<iframe|<a /);
  }
  assert.match(charts.publicUnavailable(), /暂不能使用/);
  const entry = await readFile(root+'department-ui/knowledge/public-entry.jsx','utf8');
  assert.doesNotMatch(entry, /import PublicShare|createPublicService|shareToken|location.hash/);
  const miniHome = await readFile(root+'source/miniprogram/pages/index/index.js','utf8');
  assert.doesNotMatch(miniHome, /loadKnowledgeOverview|getKnowledgeStats|createKnowledgeDistribution/);
});
