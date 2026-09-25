import {build} from 'esbuild';
import {mkdir} from 'node:fs/promises';
await mkdir(new URL('../dist/department-ui/', import.meta.url), {recursive: true});
await build({entryPoints: ['department-ui/index.jsx'], bundle: true, minify: true,
  outfile: 'dist/department-ui/app.js', format: 'iife', platform: 'browser', target: ['es2022'],
  define: {'process.env.NODE_ENV': '"production"'}, legalComments: 'linked', metafile: false});
// cache-bust：index.html 与 entry.js 里的 department-ui/app.* 带上构建时间，改版后浏览器不会再用旧缓存
import {readFileSync, writeFileSync} from 'node:fs';
const stamp = Date.now().toString(36);
for (const file of ['dist/index.html', 'dist/entry.js']) {
  const text = readFileSync(file, 'utf8').replace(/department-ui\/app\.(css|js)(\?v=[a-z0-9]+)?/g, (m, ext) => `department-ui/app.${ext}?v=${stamp}`);
  writeFileSync(file, text);
}
console.log('DEPARTMENT_UI_BUILD_OK: installed component packages bundled locally');

// Package source and generated UI together at a known customer commit.
import {execFileSync} from 'node:child_process';
let revision = 'unversioned';
try { revision = execFileSync('git', ['rev-parse', 'HEAD'], {encoding:'utf8'}).trim(); } catch {}
writeFileSync('dist/build.json', JSON.stringify({customer:'shenzhoukuntai', revision, builtAt:new Date().toISOString()},null,2)+'\n');
