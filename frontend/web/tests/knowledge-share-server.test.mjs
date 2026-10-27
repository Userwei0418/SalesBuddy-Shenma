import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,mkdir,readFile,writeFile,rm,symlink,unlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSalesWebServer} from '../server.mjs';
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(`http://127.0.0.1:${server.address().port}`)));
const close=server=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
async function fixture(run){
 const root=await mkdtemp(join(tmpdir(),'sb-knowledge-share-')),staticDir=join(root,'dist'),hits=[];
 await mkdir(join(staticDir,'knowledge-public'),{recursive:true});
 await writeFile(join(staticDir,'knowledge-share.html'),await readFile(new URL('../knowledge-share.html',import.meta.url)));
 await writeFile(join(staticDir,'knowledge-public/app.js'),'window.publicShareLoaded = true;');
 await writeFile(join(staticDir,'knowledge-public/app.css'),'body { color: navy; }');
 await writeFile(join(staticDir,'knowledge-public/app.js.LEGAL.txt'),'third-party notices');
 await writeFile(join(staticDir,'index.html'),'internal application root');
 const upstream=http.createServer((req,res)=>{hits.push(req.url);res.writeHead(200,{'content-type':'application/json'});res.end('{"ok":true}');});
 const target=await listen(upstream),server=createSalesWebServer({target:target+'/api/v1',staticDir,localLogin:null}),base=await listen(server);
 try{await run({base,server,staticDir,root,hits});}finally{await close(server);await close(upstream);await rm(root,{recursive:true,force:true});}
}
function raw(base,path,method='GET'){
 return new Promise((resolve,reject)=>{const url=new URL(base);const req=http.request({hostname:url.hostname,port:url.port,path,method},res=>{let body='';res.setEncoding('utf8');res.on('data',value=>body+=value);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body}));});req.on('error',reject);req.end();});
}
test('the generated production-format share URL and relative assets are served by the actual LOCAL server',()=>fixture(async({base,hits})=>{
 const generated=new URL('/web/knowledge-share.html#token='+'a'.repeat(48),base);
 const response=await fetch(generated);assert.equal(response.status,200);const html=await response.text();assert.match(html,/knowledge-public-root/);
 for(const reference of [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match=>match[1])){
  const asset=new URL(reference,generated);assert.ok(asset.pathname.startsWith('/web/knowledge-public/'));
  const read=await fetch(asset);assert.equal(read.status,200,asset.pathname);assert.ok((await read.text()).length);assert.match(read.headers.get('content-type'),asset.pathname.endsWith('.css')?/text\/css/:/javascript/);
 }
 const head=await fetch(new URL('/web/knowledge-public/app.js?v=frozen',base),{method:'HEAD'});assert.equal(head.status,200);assert.equal(await head.text(),'');
 assert.equal((await fetch(base+'/knowledge-share.html')).status,200);assert.equal(await (await fetch(base+'/')).text(),'internal application root');assert.deepEqual(hits,[]);
}));
test('the share mount never aliases API, auth, local login or other static application files',()=>fixture(async({base,hits})=>{
 for(const path of ['/web/api/v1/public/knowledge/share','/web/api/v1/auth/session','/web/local-login','/web/connection-status','/web/index.html','/web/knowledge-public/unknown.js','/web/knowledge-public/app.js/extra']){
  assert.equal((await raw(base,path)).status,404,path);
 }
 assert.equal((await raw(base,'/web/knowledge-share.html','POST')).status,405);assert.deepEqual(hits,[]);
 assert.equal((await fetch(base+'/api/v1/public/knowledge/share')).status,200);assert.deepEqual(hits,['/api/v1/public/knowledge/share']);
}));
test('raw and encoded traversal cannot normalize a share asset into an API call or another file',()=>fixture(async({base,hits})=>{
 for(const path of ['/web/../api/v1/auth/session','/web\\..\\api/v1/auth/session','/web/knowledge-public/../../api/v1/auth/session','/web/%2e%2e/api/v1/auth/session','/web/knowledge-public/%2e%2e/%2e%2e/index.html','/web%2fknowledge-public%2f..%2f..%2findex.html','/web/knowledge-public/%5capp.js','/web/knowledge-public/app.js%00','/web/knowledge-public/%zz']){
  assert.equal((await raw(base,path)).status,400,path);
 }
 assert.equal((await raw(base,'/web/knowledge-public/%252e%252e/index.html')).status,404);assert.deepEqual(hits,[]);
}));
test('a share asset symlink cannot escape the selected static directory',()=>fixture(async({base,root,staticDir,hits})=>{
 await writeFile(join(root,'private.txt'),'not public');await unlink(join(staticDir,'knowledge-public/app.js'));await symlink(join(root,'private.txt'),join(staticDir,'knowledge-public/app.js'));
 const response=await fetch(base+'/web/knowledge-public/app.js');assert.equal(response.status,403);assert.equal((await response.text()).includes('not public'),false);assert.deepEqual(hits,[]);
}));

test('self-hosted PDF assets support the production mount and retain path isolation',()=>fixture(async({base,staticDir,hits})=>{
 await mkdir(join(staticDir,'pdfjs/6.4.299/cmaps'),{recursive:true});
 await writeFile(join(staticDir,'pdfjs/6.4.299/pdf.worker.min.js'),'export const WorkerMessageHandler = {};');
 await writeFile(join(staticDir,'pdfjs/6.4.299/cmaps/UniGB-UTF16-H.bcmap'),'synthetic-map');
 const worker=await fetch(base+'/web/pdfjs/6.4.299/pdf.worker.min.js');assert.equal(worker.status,200);assert.match(worker.headers.get('content-type'),/javascript/);
 assert.equal((await fetch(base+'/web/pdfjs/6.4.299/cmaps/UniGB-UTF16-H.bcmap')).status,200);
 for(const path of ['/web/pdfjs/6.4.299/api/v1/auth','/web/pdfjs/6.4.299/unknown.js','/web/pdfjs/6.4.299/cmaps/%252e%252e/private'])assert.equal((await raw(base,path)).status,404);
 assert.equal((await raw(base,'/web/pdfjs/6.4.299/cmaps/%2e%2e/../../api/v1/auth')).status,400);assert.deepEqual(hits,[]);
}));
