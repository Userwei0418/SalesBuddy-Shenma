const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..');
test('official LOCAL build injects separate API and Web origins into its isolated Mini artifact',()=>{
 const script=String.raw`
import importlib.util,json,sys,tempfile
from pathlib import Path
root=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('local_dev_share_fixture',root/'tools/local/dev.py')
dev=importlib.util.module_from_spec(spec);spec.loader.exec_module(dev)
original=(root/'frontend/miniprogram/config.js').read_bytes()
files={'frontend/miniprogram/config.js':original,'frontend/miniprogram/app.json':b'{"pages":[],"window":{}}',
       'frontend/project.config.json':b'{"appid":"fixture-test-app","setting":{}}'}
for name in ('admin.html','platform.html','company-activate.html'):
 files['backend/src/sales_backend/web/'+name]=b'<title>fixture</title><body></body>'
with tempfile.TemporaryDirectory(prefix='sb-mini-share-config-') as temporary:
 dev.LOCAL=Path(temporary);dev.config=lambda:{'development_appid':'fixture-local-app'}
 outputs=[]
 for api,web in ((18822,5222),(18933,5333)):
  dev.BASE=f'http://127.0.0.1:{api}';dev.WEB_PORT=web
  target,manifest=dev.build(files=files)
  text=(dev.LOCAL/'frontend/miniprogram/config.js').read_text()
  assert (target/'frontend/miniprogram/config.js').read_bytes()==original
  assert manifest['generated_frontend_sha256']==dev.frontend_fingerprint(dev.LOCAL/'frontend')
  assert json.loads((dev.LOCAL/'frontend/project.config.json').read_text())['appid']=='fixture-local-app'
  outputs.append({'text':text,'hash':manifest['generated_frontend_sha256']})
 assert outputs[0]['hash']!=outputs[1]['hash']
 for bad in (original.replace(b'KNOWLEDGE_CUSTOMER_WEB_ORIGIN',b'MISSING_WEB_ORIGIN'),original+b'\nconst extra={KNOWLEDGE_CUSTOMER_WEB_ORIGIN:""};'):
  try:dev.build(files={**files,'frontend/miniprogram/config.js':bad})
  except RuntimeError as error:assert 'one KNOWLEDGE_CUSTOMER_WEB_ORIGIN' in str(error)
  else:raise AssertionError('missing or duplicate Web origin must fail closed')
 assert (root/'frontend/miniprogram/config.js').read_bytes()==original
 print(json.dumps(outputs))
`;
 const outputs=JSON.parse(execFileSync('python3',['-c',script,root],{encoding:'utf8',timeout:15000}));
 for(const [i,{text}] of outputs.entries()){
  const context={module:{exports:{}}};vm.runInNewContext(text,context);
  assert.equal(context.module.exports.API_BASE_URL,`http://127.0.0.1:${i?18933:18822}/api/v1`);
  assert.equal(context.module.exports.KNOWLEDGE_CUSTOMER_WEB_ORIGIN,`http://127.0.0.1:${i?5333:5222}`);
 }
});
