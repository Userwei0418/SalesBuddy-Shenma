"""Import only publishable mini-program assets; never copy account guides/config."""
from pathlib import Path
import argparse, hashlib, json, shutil, re

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('frontend', type=Path)
args = parser.parse_args()
source = args.frontend.resolve() / 'miniprogram'
target = ROOT / 'source' / 'miniprogram'
version = json.loads((args.frontend / 'VERSION.json').read_text())
manifest = {'frontend_revision': version['frontend_revision'], 'registered_pages': version['registered_pages'], 'files': {}, 'adaptations': {'config.js': 'Browser requests use the fixed same-origin /api/v1 proxy.'}}
original_config = (source / 'config.js').read_text()
enabled = re.search(r'\bHOME_CHATBI_ENABLED\s*:\s*(true|false)', original_config)
order = re.search(r'\bHOME_MESSAGE_ORDER\s*:\s*[\'\"](asc|desc)[\'\"]', original_config)
if not enabled or not order:
    raise ValueError('Verify the new source feature flags before importing this package.')
manifest['feature_flags'] = {'HOME_CHATBI_ENABLED': enabled[1] == 'true', 'HOME_MESSAGE_ORDER': order[1]}
target.mkdir(parents=True, exist_ok=True)
for path in sorted(source.rglob('*')):
    if not path.is_file():
        continue
    relative = path.relative_to(source)
    if relative.parts[0] not in ('pages', 'components', 'utils', 'styles', 'images', 'templates') and str(relative) not in ('app.js', 'app.json', 'app.wxss', 'sitemap.json'):
        continue
    if path.suffix not in ('.js', '.json', '.wxml', '.wxss', '.svg', '.png', '.jpg', '.jpeg'):
        continue
    dest = target / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    manifest['files'][relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(target / 'config.js').write_text('module.exports = ' + json.dumps({'API_BASE_URL': '/api/v1', **manifest['feature_flags']}) + ';\n')
for stale in target.rglob('*'):
    if stale.is_file() and stale.relative_to(target).as_posix() not in manifest['files'] and stale.relative_to(target).as_posix() != 'config.js':
        stale.unlink()
(ROOT / 'source' / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(f"Imported {len(manifest['files'])} source files; frontend {manifest['frontend_revision']}")
