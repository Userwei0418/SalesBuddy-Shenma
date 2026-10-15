"""Import only publishable mini-program assets; never copy account guides/config."""
from pathlib import Path
import argparse, hashlib, json, shutil, re

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('frontend', type=Path)
parser.add_argument("--files", nargs="+", help="Import only selected shared business files; preserve Web-specific source adapters")
parser.add_argument('--new-files', nargs='+', default=[], help='Explicitly reviewed additions to a selected import; requires --revision')
parser.add_argument('--purpose', default='shared-source-update', help='Record the reason for a selected import')
parser.add_argument('--revision', help='Exact reviewed source Git commit for this import')
args = parser.parse_args()
if args.revision and not re.fullmatch(r'[0-9a-f]{40}', args.revision):
    raise ValueError('Source revision must be a complete Git commit hash')
source = args.frontend.resolve() / 'miniprogram'
target = ROOT / 'source' / 'miniprogram'
version = json.loads((args.frontend / 'VERSION.json').read_text())
manifest = {'frontend_revision': version.get('frontend_revision', '4d9491637dbb785c09b5c904b57ad509dcf6ea83'), 'registered_pages': version['registered_pages'], 'files': {}, 'adaptations': {'config.js': 'Browser requests use the fixed same-origin /api/v1 proxy.'}}
original_config = (source / 'config.js').read_text()
enabled = re.search(r'\bHOME_CHATBI_ENABLED\s*:\s*(true|false)', original_config)
order = re.search(r'\bHOME_MESSAGE_ORDER\s*:\s*[\'\"](asc|desc)[\'\"]', original_config)
if not enabled or not order:
    raise ValueError('Verify the new source feature flags before importing this package.')
manifest['feature_flags'] = {'HOME_CHATBI_ENABLED': enabled[1] == 'true', 'HOME_MESSAGE_ORDER': order[1]}
def publishable(relative):
    return (relative.parts[0] in ('pages', 'components', 'utils', 'styles', 'images', 'templates', 'ui') or relative.as_posix() in ('app.js', 'app.json', 'app.wxss', 'sitemap.json', 'buildInfo.js')) and relative.suffix in ('.js', '.json', '.wxml', '.wxss', '.wxs', '.svg', '.png', '.jpg', '.jpeg') and relative.as_posix() != 'ui/VERSION.json'

if args.new_files and (not args.files or not args.revision):
    raise ValueError('New shared files require a selected import and exact reviewed revision')
if args.files:
    saved = json.loads((ROOT / 'source' / 'manifest.json').read_text())
    if saved['feature_flags'] != manifest['feature_flags']:
        raise ValueError('Selected-file import cannot change application feature flags')
    selected = args.files + args.new_files
    if len(selected) != len(set(selected)):
        raise ValueError('Selected file names must be unique')
    prepared = []
    for name in selected:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.as_posix() != name:
            raise ValueError('Selected source path must be a normalized relative path')
        if name in args.new_files:
            if name in saved['files'] or not publishable(relative):
                raise ValueError('New file must be an explicit publishable source addition')
        elif name not in saved['files']:
            raise ValueError('Selected file must already belong to the publishable source inventory')
        path = source / relative
        if path.is_symlink() or source not in path.resolve().parents or target not in (target / relative).resolve().parents:
            raise ValueError('Selected files cannot escape the reviewed source or target')
        prepared.append((name, relative, path.read_bytes()))
    # Validate/read the complete selection before touching generated files.
    for name, relative, data in prepared:
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        (target / relative).write_bytes(data)
        saved['files'][name] = hashlib.sha256(data).hexdigest()
    saved.setdefault('incremental_imports', []).append({'files': selected, 'purpose': args.purpose, **({'new_files': args.new_files} if args.new_files else {}), **({'revision': args.revision} if args.revision else {})})
    (ROOT / 'source' / 'manifest.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(selected)} selected business files')
    raise SystemExit(0)

target.mkdir(parents=True, exist_ok=True)
for path in sorted(source.rglob('*')):
    if not path.is_file():
        continue
    relative = path.relative_to(source)
    # ``ui`` is part of the shared Mini Program source.  Web pages reference
    # these components by their absolute ``/ui/...`` paths, so excluding the
    # directory produces a bundle that can pass static checks but fails as
    # soon as a shared component is rendered.  Keep the import bounded to the
    # same publishable asset types as the rest of the Mini Program source.
    if not publishable(relative):
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
