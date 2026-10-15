// Isolated React rendering with synthetic data; every request is intercepted.
import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
let browser, js, css;
before(async () => {
  const output = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
    import React,{useState} from 'react'; import {createRoot} from 'react-dom/client'; import {App} from 'antd';
    import {PcProvider} from './department-ui/pc/PcProvider.jsx'; import Profile from './department-ui/Profile.jsx';
    import './runtime.css'; import '@shandiant/tokens/css'; import '@shandiant/ui-react/style.css'; import './department-ui/shared.css';
    import './department-ui/pc/tokens.css'; import './department-ui/pc/pc-theme.css';
    const dates=['2026-10-01','2026-10-02','2026-10-03','2026-10-07','2026-10-09'];
    const base={activeProfileTab:'profile',userName:'合成成员',initial:'合',roleName:'客户经理',team:'合成团队',profileTabs:[{key:'profile',label:'成长与评估'}],profileScopeLabel:'本人',profileScopeMode:'person',growthReady:true,growthStatusText:'已保存结果',overallScore:'68',reviewDate:'2026-10-09',visitCount:12,reviewSummary:'已根据归档记录整理能力评估。',selectedGrowthCode:'overall',growthOptions:[{code:'overall',name:'综合'}],dimensions:[{code:'need',name:'需求挖掘',score:68,assessment:'本期有客户需求核实记录，采购决策链仍待补充。',coachingAction:'下次跟进核实采购决策链。'},{code:'plan',name:'行动计划',score:null,assessment:'缺少可验证的下一步计划。',coachingAction:'补充明确的负责人和时间。'}],aiAdvice:['继续积累有来源的正式跟进记录。'],history:dates.map((review_date,i)=>({id:'review-'+i,review_date,overall_score:99,score_summary:{value:[60,65,null,70,80][i]},score_basis:{snapshot_status:'frozen',basis_id:i===4?'rule-2':'rule-1',framework_version:i===4?2:1}}))};
    const fde={data:{ready:true,loading:false,sampleCount:2,updatedText:'2026-10-09',dimensions:[{code:'record_quality',name:'记录质量',shortName:'记录质量',score:50,scoreText:'50',width:50,assessment:'已评分记录的均值'},{code:'plan_coverage',name:'计划覆盖率',shortName:'计划',score:null,scoreText:'未评估',width:0,assessment:'没有可评估材料'},...['project_coverage','customer_coverage','task_completion','task_timeliness'].map((code,i)=>({code,name:['项目覆盖','客户覆盖','任务完成','按期完成'][i],score:null,scoreText:'未评估',width:0,assessment:'没有可评估材料'}))],historyPoints:[{date:'2026-10-01',score:50},{date:'2026-10-09',score:null}],historyCode:'record_quality',advice:[]}};
    function Fixture(){const[kind,set]=useState('sales'),[extra,setExtra]=useState({}); window.setGrowthKind=set; window.setGrowthExtra=setExtra; window.growthCalls ||= []; return <PcProvider><App><main className="department-ui" style={{padding:24,minHeight:'100vh'}}><Profile page={{}} data={{...base,...extra,isFde:kind==='fde',role:kind==='fde'?'fde':'sales',roleName:kind==='fde'?'售前/FDE':'客户经理'}} invoke={(name,payload)=>window.growthCalls.push({name,payload})} invokeOn={()=>{}} select={selector=>selector==='fde-profile'?fde:null}/></main></App></PcProvider>};
    createRoot(document.getElementById('root')).render(<Fixture/>);
  `}, bundle: true, write: false, outdir: '.runtime/growth-components', define: {'process.env.NODE_ENV':'"production"'}});
  js = output.outputFiles.find(file => file.path.endsWith('.js')).text;
  css = output.outputFiles.find(file => file.path.endsWith('.css')).text;
  browser = await chromium.launch({channel:'chrome',headless:true});
});
after(async () => browser?.close());
for (const width of [1440, 1024]) test(`growth assessment display at ${width}px preserves dates, gaps and FDE meaning`, async () => {
  const context = await browser.newContext({viewport:{width,height:1000}}), page = await context.newPage(), errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    if (route.request().resourceType() !== 'document') { unexpected.push(route.request().url()); return route.abort(); }
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><style>${css}</style></head><body style="margin:0"><div id="page-root"><div id="root"></div></div><script>${js}</script></body></html>`});
  });
  try {
    await page.goto('https://growth-render.test');
    await page.getByRole('columnheader',{name:'评估说明'}).waitFor();
    await page.getByText('本期有客户需求核实记录，采购决策链仍待补充。').waitFor();
    assert.equal(await page.locator('.ds-pf-spark-dot').count(),4);
    assert.equal(await page.locator('.ds-pf-spark-line').count(),1);
    const xs=await page.locator('.ds-pf-spark-dot').evaluateAll(nodes=>nodes.map(node=>Number(node.getAttribute('cx'))));
    assert.ok(xs[2]-xs[1]>(xs[1]-xs[0])*4);
    await page.locator('.ds-pf-history-details summary').click();
    await page.getByRole('cell',{name:'能力框架 V2',exact:true}).waitFor();
    assert.deepEqual(await page.evaluate(()=>window.growthCalls),[]);
    await page.evaluate(()=>setGrowthExtra({growthReview:{canReview:true,reviewId:'r',evaluationState:'partially_assessed',evidenceCoverage:{status:'known',used_visits:80,total_eligible_visits:81,limit:80,truncated:true}}}));
    await page.getByText(/仅选取最近 80 条/).waitFor();
    if(process.env.GROWTH_SHOTS){await fs.mkdir(process.env.GROWTH_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.GROWTH_SHOTS,`coverage-${width}.png`),fullPage:true});}
    await page.getByRole('button',{name:'更新我的评估'}).click();
    assert.equal((await page.evaluate(()=>window.growthCalls)).at(-1).name,'requestGrowthReview');
    await page.evaluate(()=>setGrowthExtra({growthReview:{canReview:true,reviewId:'r',currentReview:{status:'queued'}}}));
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent.includes('更新我的评估')&&button.disabled));
    assert.equal(await page.getByRole('button',{name:'更新我的评估'}).isDisabled(),true);
    await page.evaluate(()=>setGrowthExtra({growthEvidence:{visible:true,reviewId:'r',result:{assessment_kind:'sales_competency',subject:{name:'合成成员'},review:{id:'r',summary:'可读评估摘要'},evidence:[{readable:false,detail:'FORBIDDEN_DETAIL'},{readable:true,visit_id:'00000000-0000-4000-8000-000000000001',detail:'可核对依据'}]}}}));
    await page.locator('.ds-pf-evidence li').filter({hasText:'可核对依据'}).waitFor();
    await page.waitForFunction(()=>{const r=document.querySelector('.ant-drawer-content-wrapper')?.getBoundingClientRect();return r&&r.right<=innerWidth+1&&r.left>=0;});
    assert.equal(await page.getByText('FORBIDDEN_DETAIL').count(),0);
    if(process.env.GROWTH_SHOTS)await page.screenshot({path:path.join(process.env.GROWTH_SHOTS,`evidence-${width}.png`)});
    await page.getByRole('button',{name:'查看原跟进'}).click();
    assert.deepEqual((await page.evaluate(()=>window.growthCalls)).at(-1),{name:'openGrowthSource',payload:{dataset:{kind:'visit',id:'00000000-0000-4000-8000-000000000001'}}});
    await page.evaluate(()=>setGrowthExtra({growthEvidence:{visible:true,reviewId:'r',error:'当前无权查看依据',result:{review:{id:'r',summary:'STALE_SUMMARY'},evidence:[{readable:true,detail:'STALE_DETAIL'}]}}}));
    await page.getByText('当前无权查看依据',{exact:true}).waitFor();
    assert.equal(await page.getByText(/STALE_SUMMARY|STALE_DETAIL/).count(),0);
    await page.evaluate(()=>setGrowthExtra({}));
    await page.getByText('当前无权查看依据',{exact:true}).waitFor({state:'hidden'});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(process.env.GROWTH_SHOTS){await fs.mkdir(process.env.GROWTH_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.GROWTH_SHOTS,`sales-${width}.png`),fullPage:true});}
    await page.evaluate(()=>setGrowthKind('fde'));
    await page.getByRole('heading',{name:'我 · FDE 工作画像'}).waitFor();
    assert.equal(await page.locator('.ds-pf-score').count(),0);
    assert.equal(await page.locator('.ds-pf-radar-shape').count(),0);
    assert.equal(await page.locator('.ds-pf-radar circle').count(),1);
    assert.equal(await page.locator('.ds-pf-spark-dot').count(),2);
    await page.getByRole('columnheader',{name:'事实指标'}).waitFor();
    if(process.env.GROWTH_SHOTS)await page.screenshot({path:path.join(process.env.GROWTH_SHOTS,`fde-${width}.png`),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  } finally {await context.close();}
});
