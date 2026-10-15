import {build} from 'esbuild';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {buildOutput, webRoot} from './build-output.mjs';
const output = buildOutput();
await mkdir(join(output, 'department-ui'), {recursive: true});
await build({absWorkingDir: webRoot, entryPoints: ['department-ui/index.jsx'], bundle: true, minify: true,
  outfile: join(output, 'department-ui/app.js'), format: 'iife', platform: 'browser', target: ['es2022'],
  define: {'process.env.NODE_ENV': '"production"'}, legalComments: 'linked', metafile: false});
// cache-bust：index.html 与 entry.js 里的 department-ui/app.* 带上构建时间，改版后浏览器不会再用旧缓存
import {readFileSync, writeFileSync} from 'node:fs';
const stamp = Date.now().toString(36);
for (const file of [join(output, 'index.html'), join(output, 'entry.js')]) {
  const text = readFileSync(file, 'utf8').replace(/department-ui\/app\.(css|js)(\?v=[a-z0-9]+)?/g, (m, ext) => `department-ui/app.${ext}?v=${stamp}`);
  writeFileSync(file, text);
}
console.log('DEPARTMENT_UI_BUILD_OK: installed component packages bundled locally');
