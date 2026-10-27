// Generated shared Page -> Web display boundary, using synthetic financial facts.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import {insightAmount, insightCell} from '../department-ui/insights-presentation.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const bundle=fs.readFileSync(path.resolve(root,process.env.SALES_WEB_DIST_DIR || 'dist','bundle.js'),'utf8');
function pageFor(raw) {
  let page;
  const session={userId:'synthetic-member',userName:'合成成员',workspaceId:'synthetic-company',role:'sales',permissions:{'dashboard.read':true}};
  const context=vm.createContext({window:{},console,Date,setTimeout,clearTimeout,Page:definition=>{page=definition;},getApp:()=>({globalData:{session}}),wx:{}});
  vm.runInContext(bundle,context);
  const cache=new Map();
  function load(name,from='') {
    const id=path.posix.normalize(name.startsWith('.')?path.posix.join(path.posix.dirname(from),name):name).replace(/\.js$/,'');
    if(cache.has(id))return cache.get(id).exports;
    const source=context.window.SALES_BUNDLE.modules[id];
    assert.equal(typeof source,'string',`generated dependency ${id}`);
    const module={exports:{}};cache.set(id,module);
    vm.runInContext(`(function(require,module,exports){${source}\n})`,context)(name=>load(name,id),module,module.exports);
    return module.exports;
  }
  load('utils/businessOptions').install(JSON.parse(fs.readFileSync(new URL('../../tests/helpers/business-options.json',import.meta.url),'utf8')));
  load('pages/bi/index');
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=values=>Object.assign(page.data,values);
  Object.assign(page.data,{loading:false,loadError:'',viewMode:'personal',memberLabel:'合成成员',selectedQuarterKeys:['2026-Q1'],quarterOptions:[{key:'2026-Q1',year:2026,quarter:1,label:'2026 Q1'}]});
  page._raw={opportunities:[],quarter_forecasts:[],quarter_actuals:[],recent_visits:[],...raw};
  page.rebuildFacts();return page.data;
}
const keys=['actual_recognized_exclusive','actual_collection','forecast_recognized_inclusive','forecast_collection'];
const cents=text=>{
  const match=String(text).match(/([\d,]+(?:\.\d+)?)\s*(万元|万|元)/);
  assert.ok(match,`金额必须标明单位：${text}`);
  return Math.round(Number(match[1].replaceAll(',',''))*(match[2]==='元'?100:1000000));
};
const actual=(recognized,collection,extra={})=>({year:2026,quarter:1,recognized_amount:recognized,recognized_confirmed_subtotal:recognized,recognized_complete:recognized!==null,collection_amount:collection,collection_confirmed_subtotal:collection,collection_complete:collection!==null,...extra});

test('generated dashboard cards and financial/ACV detail rows retain the same cents above ten thousand yuan',()=>{
  for(const amount of ['9999.99','10000.01','12345.67','123456.78']) {
    const data=pageFor({quarter_actuals:[actual(amount,amount)],quarter_forecasts:[{year:2026,quarter:1,recognized_amount:amount,collection_amount:amount}],opportunities:[{id:'synthetic-opportunity',name:'合成商机',amount,probability:70,status:'open',expected_close_date:'2026-02-03'}]});
    for(const key of keys) {
      const card=insightAmount(data.kpis.find(item=>item.key===key).value);
      assert.equal(Math.round(card.value*(card.unit==='元'?100:1000000)),Math.round(Number(amount)*100));
      assert.equal(cents(insightCell(data.metricDetails[key].rows[0].amount)),Math.round(Number(amount)*100));
    }
    for(const key of ['opportunity_acv','weighted_opportunity_acv','high_probability_acv'])assert.equal(cents(insightCell(data.metricDetails[key].rows[0].amount)),Math.round(Number(amount)*100));
  }
});

test('generated precise details preserve partial amounts, zero and unknown separately',()=>{
  const data=pageFor({quarter_actuals:[actual(null,0,{recognized_confirmed_subtotal:'12345.67',recognized_incomplete_period_count:1})],quarter_forecasts:[{year:2026,quarter:1,recognized_amount:null,collection_amount:0}]});
  const detail=key=>insightCell(data.metricDetails[key].rows[0].amount);
  assert.match(detail(keys[0]),/小计/);assert.equal(cents(detail(keys[0])),1234567);
  assert.match(data.kpis.find(item=>item.key===keys[0]).label,/小计/);
  assert.equal(cents(detail(keys[1])),0);assert.equal(detail(keys[2]),'未填写');assert.equal(cents(detail(keys[3])),0);
  assert.match(data.metricDetails[keys[0]].note,/按季度汇总/);
});
