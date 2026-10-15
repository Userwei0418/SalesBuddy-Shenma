import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {contentProblem,editItem} from '../department-ui/work-report/content.js';
import {sample,clone} from './review-report-fixtures.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const compiled=buildSync({absWorkingDir:root,entryPoints:['department-ui/work-report/DocumentReader.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const module=new Module(root+'review-integration.cjs');module.filename=root+'review-integration.cjs';module.paths=Module._nodeModulePaths(root);module._compile(compiled,module.filename);
const Reader=module.exports.default,slides=module.exports.reportSlides;
const render=props=>renderToStaticMarkup(React.createElement(Reader,props));
const propsFor=document=>({value:document.revision.content,reviewFacts:document.review_facts,status:document.status,periodLabel:document.period.label,scopeLabel:document.scope.label});
test('valid typed daily remains saveable without ordinary prose and edits keep typed facts exact',()=>{
 const daily=sample().document.revision.content;assert.equal(contentProblem(daily),'');
 assert.match(contentProblem({...daily,review_content:undefined}),/至少/);
 const bad=clone(daily);bad.review_content.items[0].fact.measure_refs=[];assert.match(contentProblem(bad),/引用/);
 const weekly=sample('weekly').document.revision.content,edited=editItem(weekly,'summary',0,'人工校对正文');
 assert.deepEqual(edited.review_content,weekly.review_content);assert.equal(edited.sections[0].items[0].kind,'manual');
});
test('saved daily reads typed content and projects one item per page without empty ordinary chapters',()=>{
 const doc=sample().document,item=doc.revision.content.review_content.items[0];doc.revision.content.review_content.items=[item,clone(item),clone(item)];
 const props=propsFor(doc),before=JSON.stringify(props),html=render(props),pages=slides(props).filter(row=>row.key.startsWith('review-daily:'));
 assert.match(html,/9007199254740993.12345678/);assert.match(html,/无可信历史基线/);assert.doesNotMatch(html,/本节尚无正文|<textarea/);
 assert.equal(pages.length,3);for(const page of pages){const shown=renderToStaticMarkup(page.content);assert.equal((shown.match(/class="rr-card"/g)||[]).length,1);assert.match(shown,/待人工确认/);}
 assert.equal(JSON.stringify(props),before);
});
test('enhanced weekly preserves ordinary prose, separately projects each group and blocks broken frozen references',()=>{
 const doc=sample('weekly').document,props=propsFor(doc),html=render({...props,onExport(){},onShare(){}});
 assert.match(html,/原普通周报内容保持不变/);assert.match(html,/指标与 KR 周报回顾/);assert.match(html,/贡献声明/);
 const pages=slides(props);assert.equal(pages.filter(row=>row.key.startsWith('review-weekly:')).length,1);assert.ok(pages.some(row=>row.group==='本期概况'));
 const bad=clone(props);bad.reviewFacts.evidence=[];const shown=render({...bad,onExport(){},onShare(){}});assert.match(shown,/不属于此报告快照/);assert.ok(slides(bad).some(row=>row.key==='review-error'));
 for(const label of ['复制与下载','确认并共享'])assert.match(shown,new RegExp('<button[^>]*disabled[^>]*>[\\s\\S]*?<span>'+label+'<\\/span>'));
});
