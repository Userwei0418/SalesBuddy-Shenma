/** Synthetic UI fixtures only: no real backend, credentials, providers or shared runtime. */
import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const output=process.env.KNOWLEDGE_EVIDENCE_DIR;
if(output) await mkdir(output,{recursive:true});
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import access from '../miniprogram/utils/access.js';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import Knowledge from './department-ui/Knowledge.jsx';
import {previewName} from './department-ui/knowledge/model.js';import {unavailableService} from './department-ui/knowledge/service.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
const option=(code,label=code)=>({code,label,active:true});
window.actor={workspaceId:id(1),userId:id(2),permissionVersion:1,permissions:{'knowledge.catalog_manage':true},permissionGrants:[{permission_code:'knowledge.catalog_manage',effect:'allow',scope_code:'workspace'}]};window.categoryDenied=false;window.calls=[];window.revoked=false;window.conflict=false;window.delay=false;window.retrievalReady=false;window.uploadFailures=0;window.candidateDenied=false;window.slowCandidates=false;
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:key=>access.can(actor,key)},require:name=>name==='utils/access'?access:{}};window.getApp=()=>SalesRuntime.app;
const catalog={company_id:id(1),revision:2,general_product_code:'general',products:[option('general','通用'),option('demo','测试产品')],industries:[option('industry','测试行业')],reporting_positions:[option('position','测试岗位')],reporting_levels:[option('level','测试职级')],decision_roles:[option('role','决策者')]};
const selection={expected_catalog_revision:2,content_category:'product_intro',content_scope:'customer',product_code:'general',content:{file_ids:[id(6)],links:[]},supplementary_text:'',remarks:''};
let stored={material_id:id(4),content_name:'产品介绍｜对客',material_revision:1,acl_revision:1,active_version_id:null,sharing_label:'个人库',capabilities:{read:true,edit:true,manage:true},acl:{grants:[],denied_user_ids:[]},versions:[{version_id:id(5),version_no:1,state:'saved',uploaded_at:'2026-10-17T01:00:00Z',contributor_user_id:id(2),contribution:{selection,content_name:'产品介绍｜对客'},files:[{file_id:id(6),filename:'fixture.pdf',media_type:'application/pdf',preview_supported:true}]}]};
window.readStored=()=>structuredClone(stored);
window.seedSharingHistory=()=>{stored={...stored,acl:{grants:[{subject_kind:'team',subject_id:id(91),actions:['read']}],denied_user_ids:[id(92)]}};};
window.newerVersion=()=>{stored={...stored,active_version_id:id(5),active_version_state:'retrievable',versions:[{...stored.versions[0],version_id:id(12),version_no:2,state:'failed'},{...stored.versions[0],state:'retrievable'}]};};
const service={...unavailableService,
 status:async()=>({retrieval:{status:retrievalReady?'available':'unconfigured',message:'知识检索尚未配置'},generation:{status:'unconfigured',message:'材料生成尚未配置',formats:[]}}),
 ask:async body=>(calls.push(['ask',body]),{status:'answered',answer:'带真实出处结构的fixture回答',citations:[{material_id:id(4),version_id:id(5),version_no:1,material_name:'测试引用材料',location:'第2页第3段'}]}),
 getCatalog:async()=>catalog,
 listMaterials:async args=>{calls.push(['list',args]);return {items:retrievalReady?[{material_id:id(4),version_id:id(12),version_no:2,active_version_id:id(5),active_version_state:'retrievable',content_name:'测试引用材料',state:'failed'}]:[],total:retrievalReady?1:0,capabilities:{create:true}};},
 getStats:async()=>({source:'Synthetic fixture',observed_at:'2026-10-17T01:00:00Z',metrics:{total:{status:'available',count:0,source:'fixture',observed_at:'2026-10-17T01:00:00Z'}}}),
 getMaterial:async id=>{calls.push(['detail',id]);if(revoked)throw Error('权限已撤回');return stored;},
 searchCustomers:async q=>({items:[{id:id(7),name:'标准测试客户'}]}),searchOpportunities:async()=>({items:[{id:id(8),name:'标准测试商机'}]}),
 createMaterial:async(payload,key)=>{calls.push(['create',payload,key]);if(categoryDenied)throw Object.assign(Error('KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED'),{code:'KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED',statusCode:403});if(delay) await new Promise(r=>window.release=r);if(conflict)throw Object.assign(Error('存在同名资料'),{code:'KNOWLEDGE_NAME_CONFLICT',candidates:[{material_id:id(4),content_name:stored.content_name,capabilities:{edit:true}}]});stored={...stored,content_name:previewName(payload,catalog,{id:id(7),name:'标准测试客户'}),versions:[{...stored.versions[0],contribution:{selection:payload,content_name:previewName(payload,catalog,{id:id(7),name:'标准测试客户'}),customer_name:payload.customer_id?'标准测试客户':null}}]};return {material_id:id(4)};},
 addVersion:async(id,body,key)=>{calls.push(['version',id,body,key]);stored={...stored,material_revision:2,versions:[{...stored.versions[0],version_id:id.replace(/4$/,'9'),version_no:2,contribution:{selection:body.contribution,content_name:previewName(body.contribution,catalog,null)}},...stored.versions]};return {material_id:id};},
 listAclCandidates:async(material_id,args)=>{calls.push(['candidates',material_id,args]);if(candidateDenied)throw Error('当前没有该材料的共享管理权限');if(slowCandidates&&args.q==='旧查询')await new Promise(resolve=>window.releaseCandidates=resolve);const rows=args.subject_kind==='team'?[{subject_id:id(10),display_name:'测试团队'}]:Array.from({length:21},(_,index)=>({subject_id:id(40+index),display_name:index===20?'财务测试成员':'测试成员'+(index+1)}));const matched=rows.filter(row=>!args.q||row.display_name.includes(args.q));return {material_id,subject_kind:args.subject_kind,items:matched.slice(args.offset,args.offset+args.limit),total:matched.length,limit:args.limit,offset:args.offset};},
 updateAcl:async(id,body,key)=>{calls.push(['acl',id,body,key]);stored={...stored,acl_revision:2,acl:body};return {material_id:id,acl_revision:2};},
 uploadFile:async(file,key)=>{calls.push(['upload',file.name,key]);if(uploadFailures-->0)throw Error('上传响应中断');return {file_id:id(11),filename:file.name};},
 readFile:async context=>{calls.push(['file',context]);if(revoked)throw Error('权限已撤回');return {blob:new Blob(['fixture'],{type:'text/plain'}),filename:'fixture.pdf',media_type:'application/pdf',preview_supported:true};},listWorks:async()=>({items:[]})};
const node=createRoot(document.getElementById('root'));window.show=()=>node.render(<PcProvider><Knowledge service={service} page={{options:{tab:'materials'}}}/></PcProvider>);show();
`},bundle:true,write:false,outdir:'.runtime/knowledge-122-browser',loader:{'.svg':'dataurl'},define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(file=>file.path.endsWith('.js')).text,css=built.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true,timeout:20000});});after(async()=>{await browser?.close();});
async function fixture(run){const context=await browser.newContext({viewport:{width:1400,height:1000}}),page=await context.newPage(),errors=[];page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));await context.route('**/*',route=>{if(route.request().url()==='http://localhost/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="UTF-8"><div class="department-ui" id="root"></div><style>'+css+'</style><script>'+js+'</script>'});return route.abort();});await page.goto('http://localhost/');try{try {await page.getByRole('button',{name:'贡献资料',exact:true}).waitFor();} catch(error) {throw Error(error.message+'; pageErrors='+JSON.stringify(errors)+'; body='+await page.locator('body').innerText());}await run(page);assert.deepEqual(errors,[]);}catch(error){if(output)await page.screenshot({path:output+'/failure.png',fullPage:true});throw Error(error.message+'; pageErrors='+JSON.stringify(errors)+'; body='+await page.locator('body').innerText(),{cause:error});}finally{await context.close();}}
async function select(page,label,text){await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();}
async function fill(page){await page.getByRole('button',{name:'贡献资料',exact:true}).click();await select(page,'内容类别','产品介绍');await select(page,'内容范围','对客');await select(page,'产品','通用');await page.getByRole('textbox',{name:'材料链接'}).fill('https://example.com/source');await page.getByRole('textbox',{name:'备注',exact:true}).click();await page.waitForTimeout(400);}
test('link contribution saves exact business input then rereads saved state and current ACL',()=>fixture(async page=>{
 await fill(page);if(output)await page.waitForTimeout(400);if(output)await page.screenshot({path:output+'/contribution.png',fullPage:true});await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();
 const calls=await page.evaluate(()=>calls);const create=calls.find(call=>call[0]==='create');assert.ok(create);assert.equal(create[1].expected_catalog_revision,2);assert.equal(create[1].content_scope,'customer');assert.equal('acl' in create[1],false);assert.ok(calls.find(call=>call[0]==='detail'));assert.ok(await page.getByText('保存成功不代表已可检索',{exact:false}).count());if(output)await page.waitForTimeout(400);if(output)await page.screenshot({path:output+'/saved-material.png',fullPage:true});
}));
test('customer-specific fields disappear and clear when changing category',()=>fixture(async page=>{
 await fill(page);await select(page,'内容类别','客户方案');await select(page,'行业','测试行业');await select(page,'CRM 标准客户','标准测试客户');await select(page,'汇报对象岗位','测试岗位');await select(page,'汇报对象职级','测试职级');await select(page,'汇报对象决策角色','决策者');await select(page,'内容类别','销售支持');assert.equal(await page.getByRole('combobox',{name:'CRM 标准客户',exact:true}).count(),0);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'销售支持｜对客',exact:true}).waitFor();const payload=await page.evaluate(()=>calls.find(call=>call[0]==='create')[1]);for(const key of ['industry_code','customer_id','reporting_position_code','reporting_level_code','decision_role_code'])assert.equal(payload[key],null);
}));
test('same name requires explicit existing-target version choice and optimistic revision',()=>fixture(async page=>{
 await page.evaluate(()=>conflict=true);await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('button',{name:'选择此资料新增版本'}).click();await page.getByRole('button',{name:'确认新增版本并保存'}).click();await page.getByRole('button',{name:'新增版本',exact:true}).waitFor();const call=await page.evaluate(()=>calls.find(call=>call[0]==='version'));assert.equal(call[2].expected_material_revision,1);assert.equal(call[1],'00000000-0000-0000-0000-000000000004');
}));
test('identity change drops pending save content and unavailable providers cannot submit',()=>fixture(async page=>{
 await page.evaluate(()=>delay=true);await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.waitForFunction(()=>typeof release==='function');await page.evaluate(()=>{actor={...actor,userId:'00000000-0000-0000-0000-000000000099'};show();release();});await page.getByRole('button',{name:'贡献资料',exact:true}).waitFor();assert.equal(await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).count(),0);await page.getByText('知识助手',{exact:true}).first().click();await page.getByText('知识检索尚未配置',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'发送'}).isDisabled(),true);await page.getByText('生成材料',{exact:true}).first().click();await page.getByRole('button',{name:'新建材料',exact:true}).click();await page.getByText('材料生成尚未配置',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'提交生成任务'}).isDisabled(),true);if(output)await page.waitForTimeout(400);if(output)await page.screenshot({path:output+'/unconfigured-generation.png',fullPage:true});
}));
test('download and preview request current authorization; revocation clears reread content',()=>fixture(async page=>{
 await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();await page.evaluate(()=>revoked=true);await page.getByRole('button',{name:/预\s*览/,exact:true}).click();await page.getByText('权限已撤回',{exact:true}).waitFor();await page.getByRole('button',{name:/下\s*载/,exact:true}).click();await page.getByText('权限已撤回',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.filter(call=>call[0]==='file').length),2);await page.getByRole('button',{name:'重新读取',exact:true}).click();await page.getByText('权限已撤回',{exact:true}).waitFor();assert.equal(await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).count(),0);
}));

test('team ACL is saved independently with its own revision and reread',()=>fixture(async page=>{
 await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();await select(page,'共享对象','测试团队');await page.getByRole('button',{name:'添加授权',exact:true}).click();await page.getByRole('button',{name:'保存共享权限',exact:true}).click();await page.waitForFunction(()=>calls.some(call=>call[0]==='acl'));const body=await page.evaluate(()=>calls.find(call=>call[0]==='acl')[2]);assert.equal(body.expected_acl_revision,1);assert.deepEqual(body.grants,[{subject_kind:'team',subject_id:'00000000-0000-0000-0000-000000000010',actions:['read']}]);assert.equal('content_scope' in body,false);await page.waitForFunction(()=>calls.filter(call=>call[0]==='detail').length>=3);
}));

test('citation opens its exact source version even after a newer material version exists',()=>fixture(async page=>{
 await page.evaluate(()=>{retrievalReady=true;newerVersion();});await page.getByText('知识助手',{exact:true}).first().click();await select(page,'选择参考材料','测试引用材料 · 当前检索版本');await page.getByRole('textbox',{name:'客户或商机背景'}).fill('测试客户的沟通准备');await page.getByRole('textbox',{name:'知识问题'}).fill('材料依据是什么');await page.getByRole('button',{name:'发送'}).click();await page.getByText('带真实出处结构的fixture回答',{exact:true}).waitFor();assert.match(await page.evaluate(()=>calls.find(row=>row[0]==='ask')[1].question),/用户提供的业务背景（不是资料证据）：测试客户的沟通准备/);await page.getByRole('button',{name:'[1] 测试引用材料 · V1',exact:true}).click();await page.getByText('V1 · 可检索 · 当前检索版本',{exact:true}).waitFor();assert.equal(await page.getByText('V2 · 处理失败',{exact:true}).count(),0);const query=await page.evaluate(()=>calls.find(call=>call[0]==='list'&&call[1].retrievable)[1]);assert.equal(query.retrievable,true);assert.equal(query.state,undefined);
}));

test('uncertain file upload retries the same bytes and UUID, then contributes its archived file ID',()=>fixture(async page=>{
 await page.evaluate(()=>uploadFailures=1);await page.getByRole('button',{name:'贡献资料',exact:true}).click();await select(page,'内容类别','产品介绍');await select(page,'内容范围','仅内部');await select(page,'产品','通用');await page.locator('input[type=file]').setInputFiles({name:'fixture.txt',mimeType:'text/plain',buffer:Buffer.from('fixture bytes')});await page.getByRole('button',{name:'重试该附件',exact:true}).click();await page.getByText('fixture.txt · 已归档',{exact:true}).waitFor();const calls=await page.evaluate(()=>window.calls.filter(call=>call[0]==='upload'));assert.equal(calls.length,2);assert.equal(calls[0][2],calls[1][2]);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜仅内部',exact:true}).waitFor();const payload=await page.evaluate(()=>window.calls.find(call=>call[0]==='create')[1]);assert.deepEqual(payload.content.file_ids,['00000000-0000-0000-0000-000000000011']);assert.deepEqual(payload.content.links,[]);
}));


test('object ACL candidates paginate across all member types and preserve existing grants and inactive deny',()=>fixture(async page=>{
 await page.evaluate(()=>seedSharingHistory());await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();
 await page.getByText('指定成员',{exact:true}).click();await select(page,'共享对象','测试成员1');await page.getByRole('button',{name:'下一页候选',exact:true}).click();await page.getByText('共 21 个 · 当前页 1 个',{exact:true}).waitFor();await select(page,'共享对象','财务测试成员');if(output)await page.screenshot({path:output+'/acl-candidates.png',fullPage:true});await page.getByRole('button',{name:'添加授权',exact:true}).click();await page.getByRole('button',{name:'保存共享权限',exact:true}).click();await page.waitForFunction(()=>calls.some(call=>call[0]==='acl'));
 const body=await page.evaluate(()=>calls.find(call=>call[0]==='acl')[2]);assert.deepEqual(body.denied_user_ids,['00000000-0000-0000-0000-000000000092']);assert.equal(body.grants.length,3);assert.ok(body.grants.some(row=>row.subject_id.endsWith('000000000091')));assert.ok(body.grants.some(row=>row.subject_id.endsWith('000000000040')));assert.ok(body.grants.some(row=>row.subject_id.endsWith('000000000060')));
 const pages=await page.evaluate(()=>calls.filter(call=>call[0]==='candidates'));assert.ok(pages.every(call=>call[1]==='00000000-0000-0000-0000-000000000004'));assert.ok(pages.some(call=>call[2].subject_kind==='user'&&call[2].offset===20));
}));
test('candidate permission failure is displayed and historical deny is removed only by explicit action',()=>fixture(async page=>{
 await page.evaluate(()=>{seedSharingHistory();candidateDenied=true;});await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByText('当前没有该材料的共享管理权限',{exact:true}).waitFor();assert.equal(await page.getByRole('combobox',{name:'共享对象',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'添加授权',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:'移除禁止',exact:true}).click();await page.getByRole('button',{name:'保存共享权限',exact:true}).click();await page.waitForFunction(()=>calls.some(call=>call[0]==='acl'));const body=await page.evaluate(()=>calls.find(call=>call[0]==='acl')[2]);assert.deepEqual(body.denied_user_ids,[]);assert.equal(body.grants.length,1);
}));
test('server search resets paging and a late query response cannot replace newer candidates',()=>fixture(async page=>{
 await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();await page.getByText('指定成员',{exact:true}).click();await page.getByRole('button',{name:'下一页候选',exact:true}).click();await page.getByText('共 21 个 · 当前页 1 个',{exact:true}).waitFor();
 await page.evaluate(()=>slowCandidates=true);await page.getByLabel('搜索共享对象',{exact:true}).fill('旧查询');await page.waitForFunction(()=>typeof releaseCandidates==='function');await page.getByLabel('搜索共享对象',{exact:true}).fill('财务');await page.getByText('共 1 个 · 当前页 1 个',{exact:true}).waitFor();await page.evaluate(()=>releaseCandidates());await select(page,'共享对象','财务测试成员');await page.getByRole('button',{name:'添加授权',exact:true}).click();const search=await page.evaluate(()=>calls.filter(call=>call[0]==='candidates'&&call[2].q==='财务').at(-1));assert.equal(search[2].offset,0);
}));


test('ordinary contributors see only the two permitted categories while workspace maintainers retain all seven',()=>fixture(async page=>{
 await page.getByRole('button',{name:'贡献资料',exact:true}).click();await page.getByRole('combobox',{name:'内容类别',exact:true}).click();
 await page.locator('.ant-select-dropdown:visible').getByText('会议演讲',{exact:true}).waitFor();
 assert.equal(await page.locator('.ant-select-dropdown:visible .ant-select-item-option').count(),7);
 await page.evaluate(()=>{actor={...actor,role:'general_manager',permissions:{'knowledge.catalog_manage':false},permissionGrants:[],permissionVersion:2};show();});
 await page.getByRole('button',{name:'贡献资料',exact:true}).click();await page.getByRole('combobox',{name:'内容类别',exact:true}).click();
 await page.waitForFunction(() => [...document.querySelectorAll('.ant-select-dropdown .ant-select-item-option-content')].filter(node => node.closest('.ant-select-dropdown')?.getClientRects().length).map(node => node.textContent).join('|') === '客户方案|会议演讲');
 const restrictedOptions = await page.evaluate(() => [...document.querySelectorAll('.ant-select-dropdown .ant-select-item-option-content')].filter(node => node.closest('.ant-select-dropdown')?.getClientRects().length).map(node => node.textContent));
 assert.deepEqual(restrictedOptions,['客户方案','会议演讲']);
 await page.locator('.ant-select-dropdown:visible').getByText('会议演讲',{exact:true}).click();await select(page,'内容范围','仅内部');await select(page,'产品','通用');
 await page.getByRole('textbox',{name:'材料链接',exact:true}).fill('https://example.test/member');await page.getByRole('textbox',{name:'备注',exact:true}).click();
 await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'会议演讲｜仅内部',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>calls.find(call=>call[0]==='create')[1].content_category),'conference_speech');
}));

test('historical maintained categories remain readable without silently rewriting their new-version form',()=>fixture(async page=>{
 await fill(page);await page.getByRole('button',{name:'保存材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();
 await page.evaluate(()=>{window.originalVersion=readStored().versions[0];actor={...actor,permissions:{'knowledge.catalog_manage':false},permissionGrants:[],permissionVersion:2};retrievalReady=true;show();});
 await page.getByRole('button',{name:'测试引用材料',exact:true}).click();await page.getByRole('heading',{name:'产品介绍｜对客',exact:true}).waitFor();
 await page.getByRole('button',{name:'新增版本',exact:true}).click();await page.getByText('原类别为产品介绍，当前不能以此类别新增版本。',{exact:false}).waitFor();
 assert.equal(await page.getByRole('button',{name:'确认新增版本并保存',exact:true}).isDisabled(),true);
 await page.getByRole('combobox',{name:'内容类别',exact:true}).click();
 await page.locator('.ant-select-dropdown:visible').getByText('会议演讲',{exact:true}).waitFor();
 assert.equal(await page.locator('.ant-select-dropdown:visible .ant-select-item-option-disabled').innerText(),'产品介绍');
 assert.deepEqual(await page.locator('.ant-select-dropdown:visible .ant-select-item-option:not(.ant-select-item-option-disabled) .ant-select-item-option-content').allTextContents(),['客户方案','会议演讲']);
 await page.locator('.ant-select-dropdown:visible').getByText('会议演讲',{exact:true}).click();await page.getByRole('button',{name:'确认新增版本并保存',exact:true}).click();
 await page.getByRole('heading',{name:'会议演讲｜对客',exact:true}).waitFor();
 const result=await page.evaluate(()=>({old:readStored().versions[1],original:originalVersion,body:calls.find(call=>call[0]==='version')[2]}));assert.deepEqual(result.old,result.original);assert.equal(result.body.contribution.content_category,'conference_speech');
}));

test('a server category denial stops resubmission under stale workspace capability and explains how to recover',()=>fixture(async page=>{
 await fill(page);await page.evaluate(()=>categoryDenied=true);await page.getByRole('button',{name:'保存材料',exact:true}).click();
 await page.getByText('当前只能贡献会议演讲或客户方案；其他类别需要目录维护权限。请刷新页面更新权限后再试。',{exact:true}).waitFor();
 assert.equal(await page.locator('.ant-drawer-footer button').filter({hasText:'保存材料'}).isDisabled(),true);
 assert.equal(await page.evaluate(()=>calls.filter(call=>call[0]==='create').length),1);assert.equal(await page.getByText('材料已保存并重新读取，处理状态请查看版本详情。',{exact:true}).count(),0);
 await select(page,'内容类别','会议演讲');assert.equal(await page.locator('.ant-drawer-footer button').filter({hasText:'保存材料'}).isDisabled(),false);
}));
