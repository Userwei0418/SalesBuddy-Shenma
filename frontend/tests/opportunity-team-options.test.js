const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function setup(read){
 let definition;const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');
 const app={globalData:{session:{workspaceId:'w',userId:'u',role:'operations',permissionVersion:'one',permissions:{'opportunity.create':true}}}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:x=>definition=x,require:name=>name.endsWith('apiClient')?{getOpportunityCreateOptions:read}:require(path.resolve(path.dirname(file),name)),getApp:()=>app,setTimeout,clearTimeout,wx:{}});
 const page={...definition.methods,data:JSON.parse(JSON.stringify(definition.data)),properties:{existing:null},triggerEvent(){},setData(values){for(const [key,value] of Object.entries(values)){if(key.startsWith('form.'))this.data.form[key.slice(5)]=value;else this.data[key]=value;}}};
 return {page,app};
}
test('额外授予多个团队时显示后端可选项，明确选择后随表单保留ID',async()=>{
 const {page}=setup(async()=>({teams:[{id:'a',name:'团队甲'},{id:'b',name:'团队乙'}],default_team_id:null}));
 await page.loadOwnerTeams();assert.equal(page.data.requiresOwnerTeam,true);assert.equal(page.data.form.owner_team_id,'');
 assert.equal(page.data.ownerTeams.length,2);page.changeOwnerTeam({detail:{value:1}});assert.equal(page.data.form.owner_team_id,'b');
 page.properties.existing={id:'old'};await page.loadOwnerTeams();assert.equal(page.data.requiresOwnerTeam,false);
});
test('过期团队草稿不能悄悄改归属，加载失败可重试',async()=>{
 let fail=true;const {page}=setup(async()=>{if(fail)throw Error('暂时不可用');return {teams:[{id:'new',name:'新授权团队'}],default_team_id:'new'};});
 page.data.form.owner_team_id='revoked';await page.loadOwnerTeams();assert.match(page.data.ownerTeamsError,/暂时/);
 fail=false;await page.loadOwnerTeams();assert.equal(page.data.form.owner_team_id,'');assert.equal(page.data.ownerTeamsError,'');
});
test('权限版本变化后的旧团队响应不能回填',async()=>{
 let resolve;const {page,app}=setup(()=>new Promise(r=>resolve=r));const pending=page.loadOwnerTeams();
 app.globalData.session.permissionVersion='two';resolve({teams:[{id:'old',name:'旧团队'}],default_team_id:'old'});await pending;
 assert.equal(page.data.ownerTeams.length,0);assert.equal(page.data.form.owner_team_id,undefined);
});
