// Synthetic business data and real React components. No local services or external requests.
import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,js,css;
before(async()=>{
  const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
    import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import {App} from 'antd';
    import Bi from './department-ui/Bi.jsx';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
    import shared from './tests/fixtures/insights-shared-v1.json';
    import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
    import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/overview-filters.css';
    const keys=['actual_recognized_exclusive','actual_collection','forecast_recognized_inclusive','forecast_collection','opportunity_acv','weighted_opportunity_acv','high_probability_acv','expected_close_count'];
    const names=['实际不含税确收','已登记回款','预测含税确收','预测回款','总商机 ACV','加权预测 ACV','高概率 ACV','预计关单数'];
    const details=Object.fromEntries(keys.map((key,i)=>[key,{title:names[i],scopeLabel:'合成南区',periodLabel:'2026 Q4',unit:'元',definition:i<2?'当前采用的期间实际；未登记与0区分。':'同范围所选季度的已登记资料。',summary:i<2?'按季度展示已登记汇总，未提供逐笔记录。':'商机预测与实际分别统计。',columns:[{key:'label',label:'期间/商机'},{key:'amount',label:'金额（元）',kind:'money'}],rows:[{id:'zero',label:'合成记录一',amount:'¥0.00'},{id:'missing',label:'合成记录二',amount:null},{id:'source',label:'合成长名称客户商机：年度智能业务平台项目',amount:'¥123,456.78',can_open:true}]}]));
    const base={viewMode:'team',canViewTeam:true,selectedTeamGroups:['south'],teamPickerSelected:['south'],teamLabel:'合成南区',memberLabel:'本人',selectedMemberId:'self',memberSelected:['self'],teamOptions:[{id:'all',name:'全部授权部门'},{id:'south',name:'合成南区'}],memberOptions:[{id:'self',name:'合成成员'}],quarterYearIndex:0,quarterYears:[2026],selectedQuarterKeys:['2026-Q4'],selectedQuarter:{label:'2026 Q4',year:2026,quarter:4,quarters:[4],isFullYear:false},quarterChoices:[1,2,3,4].map(q=>({key:'2026-Q'+q,quarter:q,selected:q===4})),kpis:keys.map((key,i)=>({key,label:names[i],value:i===7?'3':i===2?'未填写':'¥123,456',sub:i<2?'当前采用的期间实际':'同范围所选季度'})),metricDetails:details,sourceDate:'2026-10-10 10:00',dataModeLabel:'数据库实时统计',totalAcv:'¥123,456',activeOpportunityCount:null,quarterEmpty:false,funnel:[{name:'方案',probability:50,value:123456,count:3}],visitDays:[{weekday:'周五',count:1},{weekday:'周六',count:2}],weeklyVisitCount:3,timeline:[{month:'10月',value:123456,count:3,status:'推进'}],rankingCards:[]};
    function Fixture(){const[extra,setExtra]=useState({});window.setInsightData=setExtra;window.useSharedInsightData=()=>setExtra(shared.data);window.insightCalls ||= [];return <PcProvider><App><main className="department-ui" data-department-page="pages/bi/index"><Bi page={{openMetricRecord(){}}} data={{...base,...extra}} invoke={(name,payload)=>window.insightCalls.push({name,payload})}/></main></App></PcProvider>}
    createRoot(document.getElementById('root')).render(<Fixture/>);
  `},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/insights-components',define:{'process.env.NODE_ENV':'"production"'}});
  js=built.outputFiles.find(f=>f.path.endsWith('.js')).text;css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>browser?.close());
for(const width of [1470,1024,901])test(`经营口径与详情 ${width}px：完整金额、缺失、切换范围和单行筛选`,async()=>{
  const c=await browser.newContext({viewport:{width,height:960}}),p=await c.newPage(),errors=[],requests=[];
  p.on('pageerror',e=>errors.push(e.message));
  await c.route('**/*',r=>{if(r.request().resourceType()!=='document'){requests.push(r.request().url());return r.abort();}return r.fulfill({contentType:'text/html',body:'<!doctype html><html><head><style>'+css+'</style></head><body class="department-design"><aside class="web-sidebar"></aside><main class="web-main"><div class="web-topbar"></div><div id="page-root" class="department-host"><div id="root"></div></div></main><script>'+js+'</script></body></html>'});});
  try{
    await p.goto('https://insights-fixture.test');await p.getByRole('button',{name:'指标口径',exact:true}).waitFor();
    assert.equal(await p.getByText('不随季度筛选变化',{exact:false}).count(),1);
    assert.equal(await p.getByText('未填写',{exact:true}).count(),1);
    await p.getByRole('button',{name:'指标口径',exact:true}).click();await p.getByText('经营分析指标口径',{exact:true}).waitFor();
    assert.equal(await p.locator('.ds-insight-definitions > div').count(),8);
    await p.locator('.ant-drawer-close').click();
    await p.getByRole('button',{name:/实际不含税确收.*查看详情/}).click();
    await p.getByRole('cell',{name:'¥123,456.78',exact:true}).waitFor();
    assert.equal(await p.getByRole('cell',{name:'¥0.00',exact:true}).count(),1);
    assert.equal(await p.getByRole('cell',{name:'未登记',exact:true}).count(),1);
    await p.getByRole('button',{name:'查看记录',exact:true}).click();
    assert.deepEqual((await p.evaluate(()=>insightCalls)).at(-1),{name:'openMetricRecord',payload:{dataset:{metricKey:'actual_recognized_exclusive',rowId:'source'}}});
    await p.evaluate(()=>setInsightData({selectedTeamGroups:['north'],teamLabel:'合成北区'}));
    await p.locator('.ds-insight-drawer').waitFor({state:'hidden'});
    assert.equal(await p.getByRole('cell',{name:'¥123,456.78',exact:true}).count(),0);
    await p.evaluate(()=>setInsightData({loadError:'当前无权查看该范围'}));
    await p.getByText('当前无权查看该范围',{exact:true}).waitFor();
    assert.equal(await p.locator('.pc-kpi').count(),0);
    await p.evaluate(()=>setInsightData({}));
    await p.getByRole('button',{name:'指标口径',exact:true}).waitFor();
    const geometry=await p.evaluate(()=>({overflow:document.documentElement.scrollWidth-innerWidth,row:[...document.querySelector('.pc-filterbar-row').children].map(e=>{const b=e.getBoundingClientRect();return {y:b.y,center:b.y+b.height/2,right:b.right};})}));
    assert.ok(geometry.overflow<=1);assert.ok(Math.max(...geometry.row.map(r=>r.center))-Math.min(...geometry.row.map(r=>r.center))<2,'筛选控件保持一行');
    await p.locator('.ant-drawer-content-wrapper').waitFor({state:'hidden'});
    if(process.env.INSIGHTS_SHOTS){await fs.mkdir(process.env.INSIGHTS_SHOTS,{recursive:true});await p.screenshot({path:process.env.INSIGHTS_SHOTS+'/bi-'+width+'.png',fullPage:true});}
    if(width===1470){
      await p.evaluate(()=>useSharedInsightData());
      assert.equal(await p.locator('.pc-kpi.is-link').count(),8);
      for(let i=0;i<8;i++){
        await p.locator('.pc-kpi.is-link').nth(i).click();await p.locator('.ant-drawer-title').waitFor();
        assert.equal(await p.getByRole('button',{name:'查看记录',exact:true}).count(),0,'期间汇总不得冒充可跳转逐笔记录');
        if(i<2)await p.getByText('本明细按季度汇总展示，不代表逐笔实际记录。',{exact:true}).waitFor();
        if(i>=4)await p.getByRole('cell',{name:'10,000元',exact:true}).waitFor();
        await p.locator('.ant-drawer-close').click();await p.locator('.ant-drawer-content-wrapper').waitFor({state:'hidden'});
      }
    }
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  }finally{await c.close();}
});
