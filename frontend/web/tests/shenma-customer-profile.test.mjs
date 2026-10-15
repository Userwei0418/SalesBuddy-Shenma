import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,stat} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const read=path=>readFile(new URL(path,import.meta.url),'utf8');
const api='https://salesbuddy.shenzhoukuntai.com:28899/api/v1';

test('Shenma native identity and browser API target remain separated from the upstream deployment',async()=>{
 const config=JSON.parse(await read('../../project.config.json'));
 assert.equal(config.appid,'wx2824bdeb58528fd8');assert.equal(config.projectname,'Shenma-SalesBuddy');
 assert.ok((await read('../../miniprogram/config.js')).includes(api));
 const browserConfig=await read('../source/miniprogram/config.js');assert.match(browserConfig,/API_BASE_URL.*\/api\/v1/);assert.doesNotMatch(browserConfig,/https?:/);
 const connection=JSON.parse(await read('../connection.config.json'));assert.equal(connection.apiTarget,api);assert.equal(connection.adminUrl,'https://salesbuddy.shenzhoukuntai.com:28899/admin');
 for(const file of ['../entry.js','../runtime.js','../connection.config.json','../index.html','../privacy-notice.json','../../miniprogram/config.js'])assert.doesNotMatch(await read(file),/www\.ericepc\.com|商汤销售小浣熊|2446796988@qq\.com/,file);
 const build=JSON.parse(await read('../dist/version.json'));assert.equal(build.pages,32);assert.equal(build.components,57);
 const bundle=JSON.parse((await read('../dist/bundle.js')).replace(/^window.SALES_BUNDLE=/,'').replace(/;\s*$/,''));
 for(const route of ['pages/work-report/index','pages/customer-history/index','pages/opportunity-history/index','pages/knowledge/index','pages/weekly-report/index'])assert.ok(bundle.pages[route],route);
 assert.equal(bundle.config.window.navigationBarTitleText,'神码 SalesBuddy');
 assert.match(await read('../department-ui/pc/theme.js'),/primary: '#2863CD'/);
});

test('existing /workspace/ serves the complete build and relative assets without any upstream connection',async()=>{
 const server=createSalesWebServer({target:''});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 try{
  const redirect=await fetch(origin+'/workspace?mode=live',{redirect:'manual'});assert.equal(redirect.status,308);assert.equal(redirect.headers.get('location'),'/workspace/?mode=live');
  const response=await fetch(origin+'/workspace/');assert.equal(response.status,200);assert.match(await response.text(),/神码 SalesBuddy/);
  for(const path of ['entry.js','bundle.js','department-ui/app.js','department-ui/app.css','assets/brand/favicon.svg','version.json']){const asset=await fetch(origin+'/workspace/'+path);assert.equal(asset.status,200,path);assert.equal(Number(asset.headers.get('content-length')),(await stat(new URL('../dist/'+path,import.meta.url))).size);}
  const boot=await fetch(origin+'/web-capabilities');assert.equal(boot.status,200);assert.equal((await boot.json()).adminUrl,'');
  const apiResponse=await fetch(origin+'/api/v1/health/ready');assert.equal(apiResponse.status,503,'No implicit connection to the default customer or upstream server');
 }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
