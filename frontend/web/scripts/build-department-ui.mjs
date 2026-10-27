import {build} from 'esbuild';
import {mkdir, cp, copyFile} from 'node:fs/promises';
import {join} from 'node:path';
import {buildOutput, webRoot} from './build-output.mjs';
const output = buildOutput();
await mkdir(join(output, 'department-ui'), {recursive: true});
await build({absWorkingDir: webRoot, entryPoints: {'app': 'department-ui/index.jsx'}, bundle: true, minify: true,
  outdir: join(output, 'department-ui'), format: 'esm', splitting: true, chunkNames: 'chunks/[name]-[hash]', platform: 'browser', target: ['es2022'],
  define: {'process.env.NODE_ENV': '"production"'}, legalComments: 'linked', metafile: false});
// cache-bust：index.html 与 entry.js 里的 department-ui/app.* 带上构建时间，改版后浏览器不会再用旧缓存
import {readFileSync, writeFileSync} from 'node:fs';
const stamp = Date.now().toString(36);
for (const file of [join(output, 'index.html'), join(output, 'entry.js')]) {
  const text = readFileSync(file, 'utf8').replace(/department-ui\/app\.(css|js)(\?v=[a-z0-9]+)?/g, (m, ext) => `department-ui/app.${ext}?v=${stamp}`);
  writeFileSync(file, text);
}
console.log('DEPARTMENT_UI_BUILD_OK: installed component packages bundled locally');

await build({absWorkingDir:webRoot,entryPoints:["department-ui/knowledge/public-entry.jsx"],bundle:true,minify:true,outfile:join(output,"knowledge-public/app.js"),format:"iife",platform:"browser",target:["es2022"],define:{"process.env.NODE_ENV":"\"production\""},legalComments:"linked"});
console.log("KNOWLEDGE_PUBLIC_BUILD_OK: standalone public entry without SalesBuddy login runtime");

const publicHtml=join(output,'knowledge-share.html');
writeFileSync(publicHtml,readFileSync(publicHtml,'utf8').replace(/knowledge-public\/app\.(css|js)(\?v=[a-z0-9]+)?/g,(_,ext)=>`knowledge-public/app.${ext}?v=${stamp}`));

// PDF worker, CJK maps, fonts and decoders are pinned and served by this site.
// Keep the module worker bytes intact but use .js for existing production MIME maps.
const pdfPackage = JSON.parse(readFileSync(join(webRoot, 'node_modules/pdfjs-dist/package.json'), 'utf8'));
const pdfSource = join(webRoot, 'node_modules/pdfjs-dist'), pdfOutput = join(output, 'pdfjs', pdfPackage.version);
await mkdir(pdfOutput, {recursive: true});
await copyFile(join(pdfSource, 'legacy/build/pdf.worker.min.mjs'), join(pdfOutput, 'pdf.worker.min.js'));
await copyFile(join(pdfSource, 'LICENSE'), join(pdfOutput, 'LICENSE'));
for (const directory of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) await cp(join(pdfSource, directory), join(pdfOutput, directory), {recursive: true});
console.log(`KNOWLEDGE_PDF_BUILD_OK: self-hosted pdfjs-dist ${pdfPackage.version}`);
