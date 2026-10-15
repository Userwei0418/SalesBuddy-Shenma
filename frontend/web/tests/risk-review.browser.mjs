// Synthetic rows + actual Web components. Every network request is intercepted.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,js,css;
before(async()=>{
  const result=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
    import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import {flushSync} from 'react-dom';import {App} from 'antd';
    import {Risks,RiskDetail} from './department-ui/RiskReview.jsx';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
    import './runtime.css';import './desktop.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
    import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/overview-filters.css';
    const risk={id:'risk-1',title:'预算尚未确认，签约时间存在延期风险',customerName:'合成客户',description:'本周方案已沟通，预算决策尚未确认。',owner:'合成成员',team:'合成南区',status:'accepted',severity:'高',openedLabel:'2026/10/8',version_no:4,evidence:[{detail:'预算审批待客户确认',source_ids:['internal-hidden']}],suggested_action:'联系客户确认预算审批负责人和时间。',canResolve:false,canDecide:true,current_decision:{decision:'defer',note:'等待新预算',actor_name:'合成成员',occurred_at:'2026-10-09T08:00:00Z',is_current:false},events:[{id:'event-1',event_type:'suggestion_decided',actor_name:'合成成员',occurred_at:'2026-10-09T08:00:00Z',payload:{decision:'defer',note:'等待新预算',suggested_action:'核实预算',request_id:'internal-hidden'}}]};
    const listing={scopeMode:'all',scopeOptions:[{value:'all',label:'全部授权范围'},{value:'self',label:'个人'},{value:'team',label:'部门'},{value:'person',label:'成员'}],teamOptions:[{id:'team-uuid-1',name:'合成南区'}],memberOptions:[{id:'member-uuid-1',name:'同名成员',teamLabel:'合成南区',account_code:'XS001'}],selectedMemberId:'member-uuid-1',activeTab:'open',tabs:[{key:'all',label:'全部'},{key:'open',label:'未关闭'},{key:'resolved',label:'已解除'}],summary:{total:150,open_count:130,resolved_count:20,overdue_count:7},total:130,pageSize:50,offset:0,filteredRisks:[risk]};
    function Fixture(){const[state,setState]=useState({mode:'list',data:{}});window.showRisk=(mode,data={})=>flushSync(()=>setState({mode,data}));window.riskCalls ||= [];const detail=state.mode==='detail',data={...(detail?{risk,canResolveRisk:false,decisionChoice:'adopt',decisionNote:'先联系预算负责人'}:listing),...state.data};return <PcProvider><App><main className="department-ui" data-department-page={detail?'pages/risk-detail/index':'pages/risks/index'}>{React.createElement(detail?RiskDetail:Risks,{data,invoke:(name,payload)=>window.riskCalls.push({name,payload})})}</main></App></PcProvider>}
    createRoot(document.getElementById('root')).render(<Fixture/>);
  `},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/risk-components',define:{'process.env.NODE_ENV':'"production"'}});
  js=result.outputFiles.find(f=>f.path.endsWith('.js')).text;css=result.outputFiles.find(f=>f.path.endsWith('.css')).text;
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>browser?.close());
for(const width of [1470,1024,901])test(`风险 ${width}px：完整计数、UUID范围、版本冲突和处理权限`,async()=>{
  const c=await browser.newContext({viewport:{width,height:960}}),p=await c.newPage(),errors=[],requests=[];
  p.on('pageerror',e=>errors.push(e.message));
  await c.route('**/*',r=>{if(r.request().resourceType()!=='document'){requests.push(r.request().url());return r.abort();}return r.fulfill({contentType:'text/html',body:'<!doctype html><html><head><style>'+css+'</style></head><body class="department-design"><aside class="web-sidebar"></aside><main class="web-main"><div class="web-topbar"></div><div id="page-root" class="department-host"><div id="root"></div></div></main><script>'+js+'</script></body></html>'});});
  try{
    await p.goto('https://risk-fixture.test');await p.getByRole('heading',{name:'风险预警',exact:true}).waitFor();
    assert.match(await p.locator('.pc-kpis').innerText(),/150/);assert.match(await p.locator('.pc-kpis').innerText(),/130/);
    assert.equal(await p.getByText('● 已接受 · 未关闭',{exact:true}).count(),1);
    await p.locator('.ant-pagination-item-2').click();assert.deepEqual((await p.evaluate(()=>riskCalls)).at(-1),{name:'changePage',payload:{detail:{current:2}}});
    await p.evaluate(()=>showRisk('list',{scopeMode:'person'}));
    if(await p.locator('.pc-filterbar-more').isVisible()){
      await p.locator('.pc-filterbar-more').click();
      assert.match(await p.locator('.pc-filterbar-overflow').innerText(),/同名成员.*XS001/);
      await p.locator('.pc-filterbar-done').click();
    }else assert.match(await p.locator('.pc-risk-filters').innerText(),/同名成员.*XS001/);
    const geometry=await p.evaluate(()=>({overflow:document.documentElement.scrollWidth-innerWidth,row:[...document.querySelector('.pc-filterbar-row').children].map(e=>{const b=e.getBoundingClientRect();return b.y+b.height/2;})}));
    assert.ok(geometry.overflow<=1);assert.ok(Math.max(...geometry.row)-Math.min(...geometry.row)<2);
    if(process.env.RISK_SHOTS){await fs.mkdir(process.env.RISK_SHOTS,{recursive:true});await p.screenshot({path:process.env.RISK_SHOTS+'/risk-list-'+width+'.png'});}
    await p.evaluate(()=>showRisk('list',{loadError:'当前风险范围读取失败'}));await p.getByText('当前风险范围读取失败',{exact:true}).waitFor();
    assert.equal(await p.getByText('预算尚未确认，签约时间存在延期风险',{exact:true}).count(),0);
    assert.equal(await p.locator('.pc-kpi-value b').filter({hasText:'未取得'}).count(),4);
    await p.evaluate(()=>showRisk('detail'));await p.getByRole('button',{name:'确认处理建议',exact:true}).waitFor();
    assert.equal(await p.getByRole('button',{name:'确认解除风险',exact:true}).count(),0);
    await p.getByRole('button',{name:'确认处理建议',exact:true}).click();assert.equal((await p.evaluate(()=>riskCalls)).at(-1).name,'confirmDecision');
    assert.equal(await p.getByText('已采纳 · 已关闭',{exact:true}).count(),0);assert.equal(await p.getByText('暂不采纳 · 历史版本决定',{exact:true}).count(),1);
    await p.locator('summary').click();assert.equal(await p.getByText('建议暂不采纳',{exact:true}).count(),1);assert.doesNotMatch(await p.locator('.pc-risk-detail').innerText(),/internal-hidden|source_ids|request_id/);
    await p.evaluate(()=>showRisk('detail',{risk:{status:'open',version_no:5,current_decision:{decision:'adopt',note:'旧版本安排',is_current:true,risk_version:4}}}));
    assert.equal(await p.getByText('已采纳 · 历史版本决定',{exact:true}).count(),1,'版本变化不能仅靠旧 is_current 标志显示当前决定');
    await p.evaluate(()=>showRisk('detail',{refreshRequired:true,decisionError:'风险版本已更新'}));
    assert.equal(await p.getByRole('button',{name:'确认处理建议',exact:true}).isDisabled(),true);
    await p.getByRole('button',{name:'读取最新风险',exact:true}).click();assert.equal((await p.evaluate(()=>riskCalls)).at(-1).name,'refreshRisk');
    await p.evaluate(()=>showRisk('detail',{canResolveRisk:true}));assert.equal(await p.getByRole('button',{name:'确认解除风险',exact:true}).count(),0,'仅有操作权限没有对象权限仍不能解除');
    if(process.env.RISK_SHOTS)await p.screenshot({path:process.env.RISK_SHOTS+'/risk-detail-'+width+'.png'});
    await p.evaluate(()=>showRisk('detail',{risk:{status:'open',canResolve:true,canDecide:true,suggested_action:'确认预算',version_no:4},canResolveRisk:true,resolutionNote:'预算已完成正式审批'}));
    await p.getByRole('button',{name:'确认解除风险',exact:true}).click();assert.equal((await p.evaluate(()=>riskCalls)).at(-1).name,'confirmResolve');
    await p.evaluate(()=>showRisk('detail',{risk:{status:'open',canResolve:true,canDecide:true,suggested_action:'确认预算'},canResolveRisk:true}));
    assert.equal(await p.getByRole('button',{name:'确认解除风险',exact:true}).count(),0);assert.equal(await p.getByRole('button',{name:'确认处理建议',exact:true}).count(),0);
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  }finally{await c.close();}
});
