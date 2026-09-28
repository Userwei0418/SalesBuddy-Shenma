#!/usr/bin/env node
/**
 * 把部门小程序组件库（@shandiant/ui-miniprogram）和它依赖的 tdesign-miniprogram 子集拷进 miniprogram/ui/，
 * 做的事和开发者工具「构建 npm」一样，只是产物是普通源码目录：本工程不开 npm、校验脚本也不允许 miniprogram_npm。
 *
 * 用法（在 frontend/ 下）：node scripts/vendor_ui.mjs [--desgin ../../desgin]
 *   只拷页面实际用到的 sb-* 组件（扫描 miniprogram 下所有 .json 的 usingComponents 里 /ui/sb/ 开头的路径）及其闭包；
 *   页面直接用的 TDesign 基础控件（usingComponents 写 /ui/tdesign/<名>/<名>，如 t-checkbox、t-popup）也一并拷入；
 *   --extra checkbox,popup 预先多拷几个 TDesign 控件（改页面时试用，最后不带 --extra 再跑一次收掉）；
 *   tdesign 的 ESM 转成 CommonJS，裸模块名（tslib、dayjs）改成相对路径；图标样式只留用到的字形。
 *   变量文件 design-tokens.wxss、bridge-tdesign.wxss 同步到 miniprogram/ui/。
 * 改了组件库版本或页面新用了组件，重新跑一次即可；ui/ 目录整目录重写，不要手改里面的文件。
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const mp = path.join(root, 'miniprogram');
const arg = process.argv.indexOf('--desgin');
const desgin = path.resolve(arg > 0 ? process.argv[arg + 1] : process.env.DESGIN_DIR || path.join(root, '../../desgin'));
const pkg = path.join(desgin, 'packages/ui-miniprogram');
const require = createRequire(path.join(pkg, 'package.json'));
const esbuild = require('esbuild');
const T = path.join(pkg, 'node_modules/tdesign-miniprogram/miniprogram_dist');
const C = path.join(pkg, 'components');
const out = path.join(mp, 'ui');
const tdOut = path.join(out, 'tdesign'), sbOut = path.join(out, 'sb'), libOut = path.join(out, 'lib');
if (!fs.existsSync(T) || !fs.existsSync(C)) { console.error(`找不到组件库：${pkg}（先在该目录 npm i，或用 --desgin 指定部门仓库位置）`); process.exit(1); }
const version = JSON.parse(fs.readFileSync(path.join(pkg, 'package.json'), 'utf8')).version;
const tdVersion = JSON.parse(fs.readFileSync(path.join(pkg, 'node_modules/tdesign-miniprogram/package.json'), 'utf8')).version;

// 1. 页面用到了哪些 sb-*
const used = new Set(), usedTd = new Set(), pageIcons = new Set();
(function scan(dir) {
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f);
    if (p === out) continue;
    if (fs.statSync(p).isDirectory()) scan(p);
    else if (f.endsWith('.json')) { try { for (const v of Object.values(JSON.parse(fs.readFileSync(p, 'utf8')).usingComponents || {})) { const m = String(v).match(/\/ui\/sb\/(sb-[a-z-]+)\//); if (m) used.add(m[1]); const t = String(v).match(/\/ui\/tdesign\/([a-z-]+)\/([a-z-]+)$/); if (t) usedTd.add(t[1] + '/' + t[2]); } } catch (_) {} }
    else if (f.endsWith('.wxml')) { for (const m of fs.readFileSync(p, 'utf8').matchAll(/<t-icon[^>]*\sname="([a-z][a-z0-9-]*)"/g)) pageIcons.add(m[1]); }
  }
})(mp);
if (process.argv.includes('--all')) for (const n of fs.readdirSync(C)) if (n.startsWith('sb-')) used.add(n);
const extraArg = process.argv.indexOf('--extra');
if (extraArg > 0) for (const n of String(process.argv[extraArg + 1] || '').split(',').filter(Boolean)) usedTd.add(n.includes('/') ? n : n + '/' + n);

const iconNamesSeed = new Set();
// 2. 闭包
const files = new Set(), bare = new Set();
const tdPath = p => path.join(T, p.replace(/^tdesign-miniprogram\//, ''));
function addFile(f) {
  if (!fs.existsSync(f) || !fs.statSync(f).isFile() || files.has(f)) return; files.add(f);
  const t = fs.readFileSync(f, 'utf8'), dir = path.dirname(f);
  if (f.endsWith('.json')) { const j = JSON.parse(t); for (const p of Object.values(j.usingComponents || {})) addComp(p.startsWith('tdesign-miniprogram/') ? tdPath(p) : path.resolve(dir, p)); }
  if (f.endsWith('.js')) for (const m of t.matchAll(/(?:from\s*|require\(\s*|import\s*)['"]([^'"]+)['"]/g)) { const p = m[1]; if (p.startsWith('.')) { const r = path.resolve(dir, p); addFile(r.endsWith('.js') ? r : r + '.js'); addFile(r + '/index.js'); } else bare.add(p); }
  if (f.endsWith('.wxss')) for (const m of t.matchAll(/@import\s+['"]([^'"]+)['"]/g)) addFile(path.resolve(dir, m[1]));
  if (f.endsWith('.wxml')) for (const m of t.matchAll(/<(?:wxs|import|include)[^>]*src=['"]([^'"]+)['"]/g)) addFile(path.resolve(dir, m[1]));
  if (f.endsWith('.wxs')) for (const m of t.matchAll(/require\(['"]([^'"]+)['"]\)/g)) addFile(path.resolve(dir, m[1]));
}
function addComp(base) { for (const e of ['.json', '.js', '.wxml', '.wxss', '.wxs']) addFile(base + e); }
for (const n of used) addComp(path.join(C, n, 'index'));
for (const n of usedTd) { if (!fs.existsSync(path.join(T, n + '.json'))) { console.error('TDesign 没有这个组件：' + n); process.exit(1); } addComp(path.join(T, n)); }
if (used.size) for (const f of fs.readdirSync(path.join(C, 'style'))) addFile(path.join(C, 'style', f));
for (const n of pageIcons) iconNamesSeed.add(n);

// 3. 写出
fs.rmSync(out, { recursive: true, force: true });
const target = f => f.startsWith(T) ? path.join(tdOut, path.relative(T, f)) : path.join(sbOut, path.relative(C, f));
const libFor = spec => ({ tslib: path.join(libOut, 'tslib.js'), dayjs: path.join(libOut, 'dayjs/index.js'), 'dayjs/locale/zh-cn': path.join(libOut, 'dayjs/locale/zh-cn.js'), 'dayjs/plugin/localeData': path.join(libOut, 'dayjs/plugin/localeData.js') })[spec];
const rel = (from, to) => { let r = path.relative(path.dirname(from), to).split(path.sep).join('/'); return r.startsWith('.') ? r : './' + r; };
function rewriteBare(code, file) {
  return code.replace(/require\((['"])([^'"]+)\1\)/g, (m, q, spec) => { if (spec.startsWith('.')) return m; const lib = libFor(spec); if (!lib) throw new Error(`${file} 引用了未处理的模块 ${spec}`); return `require("${rel(file, lib).replace(/\.js$/, '')}")`; });
}
// 用到的图标名：sb-icon 对照表的值，加上闭包文件里出现的带引号的字形名
const iconNames = new Set(iconNamesSeed);
const glyphs = new Set([...fs.readFileSync(path.join(T, 'icon/icon.wxss'), 'utf8').matchAll(/\.t-icon-([a-z0-9-]+):before/g)].map(m => m[1]));
for (const f of files) { if (!/\.(js|wxml|wxs|json)$/.test(f)) continue; const t = fs.readFileSync(f, 'utf8'); for (const m of t.matchAll(/['"]([a-z][a-z0-9-]*)['"]/g)) if (glyphs.has(m[1])) iconNames.add(m[1]); for (const m of t.matchAll(/name="([a-z][a-z0-9-]*)"/g)) if (glyphs.has(m[1])) iconNames.add(m[1]); }
// checkbox composes these names at runtime; quoted-literal scanning cannot see them.
if (files.has(path.join(T, 'checkbox/checkbox.wxml'))) {
  for (const name of ['check', 'check-circle-filled', 'check-rectangle-filled', 'minus-circle-filled', 'minus-rectangle-filled']) {
    if (!glyphs.has(name)) throw new Error(`TDesign checkbox icon is missing upstream: ${name}`);
    iconNames.add(name);
  }
}
let bytes = 0;
for (const f of files) {
  const to = target(f); fs.mkdirSync(path.dirname(to), { recursive: true });
  let text = fs.readFileSync(f, 'utf8');
  if (f.endsWith('.js')) text = rewriteBare(esbuild.transformSync(text, { format: 'cjs', target: 'es2017', minifyWhitespace: true, minifySyntax: true }).code, to);
  if (f.endsWith('.json')) { const j = JSON.parse(text); if (j.usingComponents) for (const [k, v] of Object.entries(j.usingComponents)) if (v.startsWith('tdesign-miniprogram/')) j.usingComponents[k] = rel(to, path.join(tdOut, v.slice(20))); text = JSON.stringify(j, null, 2) + '\n'; }
  if (f === path.join(T, 'icon/icon.wxss')) text = text.replace(/\.t-icon-([a-z0-9-]+):before\{content:'\\[0-9A-F]+';\}\n?/g, (m, n) => iconNames.has(n) ? m : '');
  fs.writeFileSync(to, text); bytes += Buffer.byteLength(text);
}
// 4. tslib、dayjs
const needLib = [...bare].filter(b => b !== ',');
if (needLib.length) {
  const emit = (to, code) => { fs.mkdirSync(path.dirname(to), { recursive: true }); fs.writeFileSync(to, code); bytes += Buffer.byteLength(code); };
  const tsl = esbuild.transformSync(fs.readFileSync(require.resolve('tslib/tslib.js'), 'utf8'), { minifyWhitespace: true, minifySyntax: true, target: 'es2017' }).code;
  emit(libFor('tslib'), tsl);
  const dj = path.dirname(require.resolve('dayjs/package.json'));
  emit(libFor('dayjs'), fs.readFileSync(path.join(dj, 'dayjs.min.js'), 'utf8'));
  emit(libFor('dayjs/locale/zh-cn'), fs.readFileSync(path.join(dj, 'locale/zh-cn.js'), 'utf8').replace(/require\("dayjs"\)/g, 'require("../index")'));
  emit(libFor('dayjs/plugin/localeData'), fs.readFileSync(path.join(dj, 'plugin/localeData.js'), 'utf8'));
  for (const b of needLib) if (!libFor(b)) throw new Error('未处理的模块 ' + b);
}
// 5. 变量文件
const dist = path.join(desgin, 'specs/salesbuddy/02-设计变量与同步链路/dist');
for (const f of ['design-tokens.wxss', 'bridge-tdesign.wxss']) { fs.mkdirSync(out, { recursive: true }); fs.copyFileSync(path.join(dist, f), path.join(out, f)); bytes += fs.statSync(path.join(out, f)).size; }
fs.writeFileSync(path.join(out, 'VERSION.json'), JSON.stringify({ generated_by: 'scripts/vendor_ui.mjs', ui_miniprogram: version, tdesign_miniprogram: tdVersion, components: [...used].sort(), tdesign_direct: [...usedTd].sort(), icons: [...iconNames].sort(), note: '生成目录，请勿手改；页面改用组件后重新运行脚本' }, null, 2) + '\n');
console.log(`UI_VENDOR_OK ui-miniprogram ${version} · tdesign ${tdVersion} · 组件 ${used.size} 个（${[...used].sort().join('、') || '无'}）· 直接用 TDesign ${usedTd.size} 个（${[...usedTd].map(n => n.split('/')[1]).sort().join('、') || '无'}）· 文件 ${files.size} 个 · ${(bytes / 1024).toFixed(0)} KB · 图标 ${iconNames.size} 个`);
