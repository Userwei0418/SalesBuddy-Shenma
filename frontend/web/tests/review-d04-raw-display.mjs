// File-only regression over the exact synthetic backend response retained by QA.
// REVIEW_D04_RAW_PATH=<R03 response.json> node --test tests/review-d04-raw-display.mjs
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {changeRows,changeValue,changeLabel,coverageText,matchingPage} from '../department-ui/review/overview-model.js';
assert.ok(process.env.REVIEW_D04_RAW_PATH,'Set REVIEW_D04_RAW_PATH to the retained R03 backend response');
const bytes=readFileSync(process.env.REVIEW_D04_RAW_PATH);
assert.equal(createHash('sha256').update(bytes).digest('hex'),'f7707ec34bed62db03dc00e06af306da73d24600090bb0bc22a4ebcdaa6c3623');
const raw=JSON.parse(bytes),page=raw.response_samples.changes,events=page.items.flatMap(row=>row.events);
const forecast=events.find(row=>row.kind==='opportunity_forecast'),forecastRaw=forecast.changes[0];
const actorEvent=events.find(row=>row.actor_status==='available' && row.actor_display_name);
test('unchanged raw keeps quarter, unit, exact money, authorized actor and Chinese field/coverage labels',()=>{
 const snapshot=JSON.stringify(page),row=changeRows([forecast])[0];
 assert.deepEqual([row.unit,row.year,row.quarter],[forecastRaw.unit,forecastRaw.year,forecastRaw.quarter]);
 assert.deepEqual([row.before,row.after],['100.00','200.00']);assert.equal(changeLabel(row),'2026 Q4 预测回款');
 assert.deepEqual([changeValue(row,'before'),changeValue(row,'after')],['100.00 元','200.00 元']);
 assert.equal(changeRows([actorEvent],[])[0].actor,actorEvent.actor_display_name);
 assert.ok(changeRows(events).filter(row=>row.field==='name').every(row=>row.label==='商机名称'));
 assert.equal(coverageText(page),page.history_coverage.message);
 assert.equal(matchingPage(page,page.selection,{offset:page.offset,limit:page.limit,activity:page.activity}),page);
 assert.equal(page.total,null);assert.equal(page.coverage,'partial');assert.equal(JSON.stringify(page),snapshot);
});
test('known round trips and restricted actors retain their meaning; missing units are never guessed',()=>{
 const roundtrip=page.items.find(row=>row.name==='roundtrip');
 assert.deepEqual(changeRows(roundtrip.events).map(row=>[changeValue(row,'before'),changeValue(row,'after')]),[['直销','伙伴'],['伙伴','直销']]);
 const restricted=events.find(row=>row.actor_status==='restricted');assert.equal(changeRows([restricted],[])[0].actor,'未记录或不可查看');
 assert.equal(changeRows([{...restricted,actor_id:'hidden',actor_display_name:'hidden name'}],[{id:'hidden',name:'directory name'}])[0].actor,'未记录或不可查看');
 const unavailableUnit={field:'amount',before:'9007199254740993.21',after:'0.00'};
 assert.equal(changeValue(unavailableUnit,'before'),'9007199254740993.21');assert.equal(changeValue(unavailableUnit,'after'),'0.00');
 assert.equal(changeValue({...unavailableUnit,unit:'万元'},'before'),'9007199254740993.21 万元');
 assert.equal(changeValue({...unavailableUnit,unit:'元',before:null},'before'),'未记录');
 assert.match(coverageText({...page,missing_reasons:[...page.missing_reasons,'source_scope_not_fully_authorized']}),/来源读取权限/);
});
test('actual overview and meeting detail renderers show the repaired raw labels and exact values',async()=>{
 const root=fileURLToPath(new URL('../',import.meta.url));
 const bundled=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';
 import {ChangeDetails} from './department-ui/review/ReviewOverview.jsx';
 import {DetailSlide} from './department-ui/review/ReviewMeetingDeck.jsx';
 export const overview=row=>renderToStaticMarkup(<ChangeDetails row={row} members={[]}/>);
 export const meeting=page=>renderToStaticMarkup(<DetailSlide definition={{key:'updated_opportunities',label:'商机变动'}} entry={{status:'ready',value:page}} read={()=>{throw Error('No requests allowed');}} reveal={()=>{}} page={0} value={{period:page.period,scope:page.scope,members:[]}}/>);
 `},plugins:[{name:'expose-private-renderers-for-raw-test',setup(build){build.onLoad({filter:/review\/(ReviewOverview|ReviewMeetingDeck)\.jsx$/},args=>({contents:readFileSync(args.path,'utf8')+'\nexport {'+(args.path.endsWith('ReviewOverview.jsx')?'ChangeDetails':'DetailSlide')+'};',loader:'jsx'}));}}],bundle:true,platform:'node',format:'cjs',write:false,loader:{'.css':'empty','.svg':'dataurl'},define:{'process.env.NODE_ENV':'"production"'}});
 const module={exports:{}};new Function('require','module','exports',bundled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
 const record=page.items.find(row=>row.events.some(event=>event.kind==='opportunity_forecast'));
 for(const html of [module.exports.overview(record),module.exports.meeting(page)]){
 assert.match(html,/2026 Q4 预测回款/);assert.match(html,/100\.00 元/);assert.match(html,/200\.00 元/);assert.match(html,/own_owner/);assert.match(html,/商机名称/);
 }
 const meeting=module.exports.meeting(page);assert.ok(meeting.includes(page.history_coverage.message));assert.ok(!meeting.includes('history_field_coverage_unverified'));assert.match(meeting,/完整总量未知/);
});
