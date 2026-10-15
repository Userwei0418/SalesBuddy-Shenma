"""Import only publishable mini-program assets; never copy account guides/config."""
from pathlib import Path
import argparse, hashlib, json, shutil, re

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('frontend', type=Path)
parser.add_argument('--reviewed-patch', type=Path, help='Verify explicitly reviewed source edits against old/current/native SHA256; never copy or rehash unrelated files.')
args = parser.parse_args()
if args.reviewed_patch:
    manifest_path = ROOT / 'source' / 'manifest.json'
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    patch = json.loads(args.reviewed_patch.read_text())
    if not patch.get('name') or not patch.get('reason') or not patch.get('files'):
        raise ValueError('Reviewed patch needs name, reason and an explicit file allowlist')
    if patch.get('expected_manifest_sha256') != hashlib.sha256(original).hexdigest():
        raise ValueError('Manifest changed since this patch was reviewed')
    reviewed = set()
    for entry in patch['files']:
        name = entry['path']
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or name not in manifest['files'] or name in reviewed:
            raise ValueError('Unsafe, new or duplicated reviewed source path: ' + name)
        reviewed.add(name)
        if not entry.get('reason'):
            raise ValueError('Every reviewed file needs a provenance/adaptation reason: ' + name)
        if manifest['files'][name] != entry['expected_previous_sha256']:
            raise ValueError('Previous source hash mismatch: ' + name)
        current = ROOT / 'source' / 'miniprogram' / relative
        native = args.frontend.resolve() / 'miniprogram' / relative
        if hashlib.sha256(current.read_bytes()).hexdigest() != entry['expected_current_sha256']:
            raise ValueError('Current source changed after review: ' + name)
        if hashlib.sha256(native.read_bytes()).hexdigest() != entry['expected_native_sha256']:
            raise ValueError('Native source changed after review: ' + name)
    for name, old_hash in manifest['files'].items():
        if name not in reviewed and hashlib.sha256((ROOT / 'source' / 'miniprogram' / name).read_bytes()).hexdigest() != old_hash:
            raise ValueError('Unreviewed source drift: ' + name)
    for entry in patch['files']:
        manifest['files'][entry['path']] = entry['expected_current_sha256']
    manifest.setdefault('reviewed_patches', []).append(patch)
    # Validation is complete before the only write. Retain versions and every other entry.
    temporary = manifest_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(manifest_path)
    print(f"Reviewed {len(reviewed)} source patches; no source files copied; version unchanged")
    raise SystemExit(0)

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
