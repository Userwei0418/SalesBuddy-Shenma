// Regression cases adapted from independent QA mini-3bf6d334, plus implementation boundary cases.
require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const opp=require('../miniprogram/utils/opportunity');
const clone=x=>JSON.parse(JSON.stringify(x));
const line={id:'line-qa',version_no:2,material_id:'material-qa',quantity:'1',unit_price:'113',usage:'self_use',catalog_snapshot:{version_no:1,recognition_rule:'manual'}};
const row={id:'opportunity-qa',version_no:3,name:'QA 合成商机',amount:120000,expected_close_date:'2026-12-31',sales_channel:'direct',status:'open',probability:10,trade_relation:'direct'};
function component(existing,detail={items:[line],trade_relation:'direct'}){
 let definition;
 const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');
 const api={getOpportunityMaterials:async id=>({opportunity_id:id,version_no:existing.version_no,...clone(detail)})};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:d=>definition=d,require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>({globalData:{session:{}}}),wx:{},setTimeout,clearTimeout});
 const c={properties:{visitContext:true,existing:clone(existing)},data:{...clone(definition.data),form:opp.formForVisit(existing)},nameChecks:0,extraChecks:0,
  setData(fields){for(const [key,value] of Object.entries(fields)){const parts=key.split('.');let target=this.data;for(const part of parts.slice(0,-1))target=target[part];target[parts.at(-1)]=value;}},
  ...definition.methods};
 c.updateProductLines=()=>{};c.scheduleMaterialPreview=()=>{};c.emit=()=>{};
 c.validateExtras=()=>{c.extraChecks++;};c.checkName=async()=>{c.nameChecks++;return true;};
 return c;
}
function input(c,field,value){c.materialInput({currentTarget:{dataset:{key:'line-qa',field}},detail:{value}});}
test('QA-N01: separately loaded material edited then exactly restored is no-op',async()=>{
 const c=component(row);await c.retryMaterialLines();assert.equal(c.data.materialLoadError,'');
 const original=clone(c.data.form.material_lines);input(c,'quantity','2');input(c,'quantity','1');
 assert.deepEqual(c.data.form.material_lines,original);
 const result=await c.prepare();console.log('OBS QA-N01',JSON.stringify({dirty:c.data.form.materials_dirty,existingHasLines:Array.isArray(c.properties.existing.material_lines),action:result?.action??null,nameChecks:c.nameChecks}));
 assert.equal(result,null,'restored separately loaded material must not produce opportunity update');
});
test('QA-N02: numerically equivalent material input is no-op',async()=>{
 const c=component({...row,material_lines:[line]});input(c,'quantity','1.0000');input(c,'unit_price','113.00');
 const result=await c.prepare();console.log('OBS QA-N02',JSON.stringify({action:result?.action??null,quantity:result?.material_lines?.[0]?.quantity,unit_price:result?.material_lines?.[0]?.unit_price}));
 assert.equal(result,null,'equivalent material decimal values must not produce opportunity update');
});
test('QA-N03: persisted field changes survive no-op comparison',async()=>{
 const variants=[['close date',f=>f.expected_close_date='2027-01-01'],['stage',f=>f.stageIndex=1],['relation',f=>f.trade_relation='distributor'],['upstream',f=>{f.trade_relation='downstream';f.upstream_opportunity_id='upstream-qa';}],
  ...['quantity','unit_price','expected_delivery_on','note','usage'].map(k=>[k,f=>{f.materials_dirty=true;f.material_lines[0][k]=({quantity:'2',unit_price:'114',expected_delivery_on:'2026-12-01',note:'changed',usage:'resale'})[k];}]),
  ...Object.entries({priority:'high',is_key:true,trial_status:'started',summary:'changed',note:'changed',ppl_status:'active',external_system_status:'active',fde_support_stage:'initial',fde_progress:'changed',customer_source:['other'],participants:[{user_id:'user-qa',role:'sales'}]}).map(([k,v])=>[k,f=>{f.extra_dirty[k]=true;f.extra_values[k]=v;}])];
 for(const [label,change]of variants){const existing={...row,material_lines:[line]},f=opp.formForVisit(existing);change(f);assert.equal(opp.visitHasChanges(f,existing),true,label);}
 console.log('OBS QA-N03',JSON.stringify({persisted_change_variants:variants.length}));
});
test('QA-N04: invalid material edit reaches validation, not silent suppression',async()=>{
 const c=component({...row,material_lines:[line]});input(c,'quantity','0');
 await assert.rejects(()=>c.prepare(),/未大于0/);assert.equal(c.nameChecks,0);
});


test('加载中或加载失败不能被无变更分支绕过',async()=>{
 const c=component({...row,material_lines:[line]});c.data.materialLinesLoading=true;
 await assert.rejects(()=>c.prepare(),/等待物料/);c.data.materialLinesLoading=false;c.data.materialLoadError='物料读取失败';
 await assert.rejects(()=>c.prepare(),/物料读取失败/);
});
test('已保存草稿不被服务端比较基准覆盖，且真实修改保留',async()=>{
 const c=component(row);c.data.form.material_lines=[{...line,quantity:'2'}];c.data.form.materials_dirty=true;
 await c.retryMaterialLines();assert.equal(c.data.form.material_lines[0].quantity,'2');
 assert.equal((await c.prepare()).material_lines[0].quantity,'2');
});
test('同ID不同版本与不同商机不复用旧物料比较基准',async()=>{
 for(const next of [{...row,version_no:4},{...row,id:'other'}]){
  const c=component(row);await c.retryMaterialLines();input(c,'quantity','2');input(c,'quantity','1');
  c.properties.existing=next;assert.equal((await c.prepare()).action,'update');
 }
});
test('精确大数尾零可忽略，最小单位实际变化不能被浮点吞掉',()=>{
 const existing={...row,material_lines:[{...line,unit_price:'9999999999999999.98'}]};
 const f=opp.formForVisit(existing);f.materials_dirty=true;f.material_lines[0].quantity='1.0000';
 assert.equal(opp.visitHasChanges(f,existing),false);
 f.material_lines[0].unit_price='9999999999999999.99';assert.equal(opp.visitHasChanges(f,existing),true);
});
