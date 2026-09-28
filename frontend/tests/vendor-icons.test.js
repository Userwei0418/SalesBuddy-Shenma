const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const mp = path.resolve(__dirname, '../miniprogram');
const checkbox = fs.readFileSync(path.join(mp, 'ui/tdesign/checkbox/checkbox.wxml'), 'utf8');
const iconCss = fs.readFileSync(path.join(mp, 'ui/tdesign/icon/icon.wxss'), 'utf8');
const glyphs = new Set([...iconCss.matchAll(/\.t-icon-([a-z0-9-]+):before\s*\{\s*content\s*:\s*['"]\\[0-9a-f]+['"]/gi)].map(match => match[1]));
const attributes = tag => Object.fromEntries([...tag.matchAll(/([\w:-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
const iconNodes = [...checkbox.matchAll(/<t-icon\b[^>]*>/g)].map(match => attributes(match[0]));

function evaluate(binding, data) {
  const expression = binding.match(/^\{\{([\s\S]*)\}\}$/);
  return expression ? vm.runInNewContext(`(${expression[1]})`, data, {timeout: 1000}) : binding;
}

function renderedIconNames(data) {
  return iconNodes
    .filter(node => !node['wx:if'] || evaluate(node['wx:if'], data))
    .map(node => evaluate(node.name, data));
}

function assertCheckedGlyphs(icon, indeterminate, context) {
  for (const disabled of [false, true]) {
    const data = {icon, checked: true, indeterminate, disabled, _disabled: disabled};
    const names = renderedIconNames(data);
    assert.equal(names.length, 1, `${context}: the checked checkbox must render one icon`);
    for (const name of names) {
      assert.ok(glyphs.has(name), `${context}: missing vendored glyph ${name} (disabled=${disabled})`);
    }
    assert.deepEqual(renderedIconNames({...data, checked: false}), [], `${context}: unchecked uses the existing CSS outline`);
  }
}

for (const icon of ['rectangle', 'circle']) {
  for (const indeterminate of [false, true]) {
    test(`checkbox ${icon} ${indeterminate ? 'mixed' : 'checked'} retains its dynamic glyph, including disabled state`, () => {
      assertCheckedGlyphs(icon, indeterminate, `${icon}/${indeterminate ? 'mixed' : 'checked'}`);
    });
  }
}

test('checkbox line ordinary checked state retains its glyph', () => {
  // TDesign 1.16.1 has no minus-line-filled glyph; this app does not use that combination.
  assertCheckedGlyphs('line', false, 'line/checked');
});

function pageTemplates(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
    const filename = path.join(dir, entry.name);
    return entry.isDirectory() ? pageTemplates(filename) : entry.name.endsWith('.wxml') ? [filename] : [];
  });
}

const rectangleConsumers = pageTemplates(path.join(mp, 'pages')).flatMap(filename => {
  const tags = [...fs.readFileSync(filename, 'utf8').matchAll(/<t-checkbox(?=\s|\/?>)[^>]*>/g)]
    .map(match => attributes(match[0]))
    .filter(attrs => attrs.icon === 'rectangle');
  return tags.length ? [{filename, tags}] : [];
});

test('regression covers the real login and visit rectangle checkbox consumers', () => {
  const pages = new Set(rectangleConsumers.map(consumer => path.relative(mp, consumer.filename)));
  for (const required of ['pages/login/index.wxml', 'pages/visit-entry/index.wxml', 'pages/visit-confirm/index.wxml']) {
    assert.ok(pages.has(required), `missing real checkbox consumer ${required}`);
  }
});

for (const {filename, tags} of rectangleConsumers) {
  const relative = path.relative(mp, filename);
  test(`${relative}: every rectangle checkbox has a visible selected glyph`, () => {
    for (const [index, attrs] of tags.entries()) {
      assertCheckedGlyphs(attrs.icon, false, `${relative} checkbox ${index + 1}`);
    }
  });
}
