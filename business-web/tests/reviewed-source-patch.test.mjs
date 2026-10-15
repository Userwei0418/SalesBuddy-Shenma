import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const sha=value=>createHash('sha256').update(value).digest('hex');
function fixture(t){
 const root=mkdtempSync(join(tmpdir(),'s2-reviewed-patch-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const folder of ['scripts','source/miniprogram/utils','native/miniprogram/utils'])mkdirSync(join(root,folder),{recursive:true});
 writeFileSync(join(root,'scripts/import_source.py'),readFileSync(new URL('../scripts/import_source.py',import.meta.url)));
 const names=['utils/access.js','utils/untouched.js'];for(const name of names){writeFileSync(join(root,'source/miniprogram',name),'before');writeFileSync(join(root,'native/miniprogram',name),'native');}
 const manifest={frontend_revision:'frozen-baseline',files:Object.fromEntries(names.map(n=>[n,sha('before')]))};writeFileSync(join(root,'source/manifest.json'),JSON.stringify(manifest));
 const patch={name:'reviewed-s2',reason:'Explicit scoped review',expected_manifest_sha256:sha(readFileSync(join(root,'source/manifest.json'))),files:[{path:names[0],expected_previous_sha256:sha('before'),expected_current_sha256:sha('after'),expected_native_sha256:sha('native'),reason:'Preserve Web-specific source differences.'}]};
 writeFileSync(join(root,'source/miniprogram',names[0]),'after');
 const run=()=>{writeFileSync(join(root,'patch.json'),JSON.stringify(patch));return spawnSync('python3',[join(root,'scripts/import_source.py'),join(root,'native'),'--reviewed-patch',join(root,'patch.json')],{encoding:'utf8'});};
 return {root,patch,run};
}
test('reviewed source patches update only named hashes and retain source/version differences',t=>{
 const {root,run}=fixture(t),result=run();assert.equal(result.status,0,result.stderr);
 const saved=JSON.parse(readFileSync(join(root,'source/manifest.json')));assert.equal(saved.files['utils/access.js'],sha('after'));assert.equal(saved.files['utils/untouched.js'],sha('before'));assert.equal(saved.frontend_revision,'frozen-baseline');assert.equal(saved.reviewed_patches.length,1);
 assert.equal(readFileSync(join(root,'source/miniprogram/utils/access.js'),'utf8'),'after');assert.equal(readFileSync(join(root,'native/miniprogram/utils/access.js'),'utf8'),'native');
});
for(const kind of ['manifest','previous','current','native','unreviewed','unsafe'])test(`reviewed source patch refuses ${kind} drift before manifest writes`,t=>{
 const {root,patch,run}=fixture(t),manifest=join(root,'source/manifest.json');
 if(kind==='manifest')patch.expected_manifest_sha256=sha('wrong');
 if(kind==='previous')patch.files[0].expected_previous_sha256=sha('wrong');
 if(kind==='current')patch.files[0].expected_current_sha256=sha('wrong');
 if(kind==='native')patch.files[0].expected_native_sha256=sha('wrong');
 if(kind==='unreviewed')writeFileSync(join(root,'source/miniprogram/utils/untouched.js'),'unreviewed');
 if(kind==='unsafe')patch.files[0].path='../outside.js';
 const before=readFileSync(manifest,'utf8'),result=run();assert.notEqual(result.status,0);assert.equal(readFileSync(manifest,'utf8'),before);
});
