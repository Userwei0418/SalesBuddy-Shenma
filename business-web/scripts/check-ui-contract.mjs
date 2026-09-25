/** Incremental V1 enforcement: reviewed UI styles only, not vendored/legacy CSS. */
import {readFile} from 'node:fs/promises';

export function inspectStyles(css, filename) {
  const violations = [];
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const match of source.matchAll(/([\w-]+)\s*:\s*([^;{}]+)[;}]/g)) {
    const [, property, raw] = match;
    const value = raw.replace(/!important/g, '').trim();
    let reason;
    if (property === 'font-size' && !/^(var\(--ui-text-[\w-]+\)|inherit|0)$/.test(value)) reason = '字号使用 --ui-text-*';
    if (/#[\da-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(/i.test(value)) reason = '颜色使用共享 token';
    if (property === 'border-radius' && !/^(var\(--ui-radius-(control|panel)\)|0|50%|inherit)$/.test(value)) reason = '圆角使用共享 token，圆形除外';
    if (reason) violations.push(`${filename}: ${property}: ${value} — ${reason}`);
  }
  return violations;
}

export async function checkStyles() {
  const filenames = ['review-interactions.css', 'review-lists.css', 'review-forms.css', 'review-pages.css'];
  const violations = (await Promise.all(filenames.map(async name => inspectStyles(await readFile(new URL('../' + name, import.meta.url), 'utf8'), name)))).flat();
  if (violations.length) throw new Error(violations.join('\n'));
  console.log(`UI 样式契约通过：${filenames.length} 个评审样式文件；旧样式保持渐进迁移。`);
}

if (process.argv[1] && new URL('file://' + process.argv[1]).href === import.meta.url) await checkStyles();
