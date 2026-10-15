"""Exercise committed source packaging and hostile/cross-version artifacts."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit
import warnings
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('product_bundle', ROOT / 'tools/installation/release_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
APPID = 'wx0123456789abcdef'
API = 'https://customer.example.test/api/v1'


def test_packaged_current_manuals_include_their_linked_instructions():
    manuals = [path for path in (ROOT/'docs').glob('*.md') if bundle.selected(str(path.relative_to(ROOT)))]
    assert manuals
    for manual in manuals:
        for target in re.findall(r'\]\(([^)]+)\)', manual.read_text()):
            reference = urlsplit(target.strip('<>'))
            if reference.scheme or not reference.path.endswith('.md'):
                continue
            linked = (manual.parent/unquote(reference.path)).resolve()
            assert linked.is_relative_to(ROOT) and linked.is_file(), (manual.name, target)
            assert bundle.selected(str(linked.relative_to(ROOT))), (manual.name, target)


def run_git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    files = {name: b'# synthetic code\n' for name in bundle.REQUIRED}
    files['frontend/project.config.json'] = bundle.json_bytes({'appid': 'wxffffffffffffffff', 'setting': {'urlCheck': True}})
    files['frontend/miniprogram/config.js'] = b'module.exports = {API_BASE_URL: "https://previous.example.test/api/v1", HOME_CHATBI_ENABLED: false};\n'
    files['database/migrations/V165__synthetic.sql'] = b'SELECT 165;\n'
    files['database/seeds/S001__synthetic.sql'] = b'SELECT 1;\n'
    files['frontend/miniprogram/assets/logo.png'] = b'Synthetic logo bytes\x00\xff'
    # Unselected archived evidence must never enter a product artifact.
    files['docs/evidence/old-business.json'] = b'{"synthetic_business":"not for distribution"}'
    for name, content in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    run_git(root, 'init', '-q')
    run_git(root, 'config', 'user.email', 'fixture@example.test')
    run_git(root, 'config', 'user.name', 'Synthetic fixture')
    run_git(root, 'add', '.')
    run_git(root, 'commit', '-qm', 'synthetic baseline')
    return root


def prepare(source, **overrides):
    revision, files, modes = bundle.committed_files(source)
    options = {'revision': revision, 'api_url': API, 'appid': APPID, 'profile': 'private', 'product_name': 'candidate-fixture'}
    return bundle.prepare(files, modes, **{**options, **overrides})


def write_raw(path, files, *, mode=0o100644, duplicate=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for name, content in list(files.items()) + ([] if duplicate is None else [(duplicate, files[duplicate])]):
                entry = zipfile.ZipInfo(name); entry.create_system = 3; entry.external_attr = mode << 16
                archive.writestr(entry, content)
    data = output.getvalue(); path.write_bytes(data)
    return bundle.sha256(data)


def test_round_trip_is_reproducible_and_keeps_logo_and_source(source, tmp_path):
    files, modes, manifest = prepare(source)
    first, second = tmp_path/'one.zip', tmp_path/'two.zip'
    digest = bundle.write_archive(files, modes, first)
    assert bundle.write_archive(files, modes, second) == digest
    actual, checked = bundle.read_verified(first, digest)
    assert checked == manifest and checked['installation_verified'] is False
    assert 'docs/evidence/old-business.json' not in actual
    assert actual['frontend/miniprogram/assets/logo.png'] == (source/'frontend/miniprogram/assets/logo.png').read_bytes()
    assert b'previous.example.test' in (source/'frontend/miniprogram/config.js').read_bytes()
    assert API.encode() in actual['frontend/miniprogram/config.js']
    assert not (first.stat().st_mode & 0o077)
    assert manifest['expected_schema'] == 'V165'
    assert json.loads(actual['INSTALLATION_TARGET.json'])['external_integrations'] == 'unconfigured'


def test_reviewed_adoption_tools_are_packaged_without_private_maintenance_state(source, tmp_path):
    expected = {
        'backend/deploy/adopt_legacy_saas.py', 'backend/deploy/legacy_adoption_state.py',
        'backend/deploy/legacy_adoption_backup.py', 'backend/deploy/legacy_adoption_database.py',
        'backend/deploy/legacy_definer_dependencies.py',
        'backend/deploy/repair_legacy_definer_dependencies.py',
        'backend/deploy/inherit_service_environment.py', 'backend/deploy/verify_service_identity.py',
        'backend/scripts/bootstrap_platform_operator.py',
        'backend/scripts/acceptance/feishu_release_probe.py',
        'backend/scripts/acceptance/FEISHU_RELEASE_PROBE.md',
    }
    excluded = {'backend/deploy/ADOPTION.json', 'backend/deploy/credentials.json',
                'backend/deploy/production.env', 'backend/deploy/adhoc_mutation.py'}
    for name in expected | excluded:
        path = source/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# synthetic deployment fixture\n')
    run_git(source, 'add', 'backend'); run_git(source, 'commit', '-qm', 'deployment fixtures')
    files, modes, manifest = prepare(source)
    path = tmp_path/'deployment.zip'
    digest = bundle.write_archive(files, modes, path)
    checked, _ = bundle.read_verified(path, digest)
    assert expected <= checked.keys() and not excluded & checked.keys()
    assert all(name in manifest['file_hashes'] for name in expected)


@pytest.mark.parametrize('name', ['backend/src/new.py', 'backend/src/sales_backend/main.py'])
def test_uncommitted_new_or_changed_source_is_not_silently_packaged(source, name):
    (source/name).write_text('dirty fixture')
    with pytest.raises(bundle.BundleError, match='Commit all changes'):
        bundle.committed_files(source)


@pytest.mark.parametrize('name', ['backend/src/.env', 'backend/src/key.pem', 'backend/src/id_ed25519', 'backend/src/backups/data.sql'])
def test_even_committed_private_paths_are_refused(source, name):
    path = source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic private material')
    run_git(source, 'add', name); run_git(source, 'commit', '-qm', 'private fixture')
    with pytest.raises(bundle.BundleError, match='private artifact path'):
        bundle.committed_files(source)


def test_source_symlink_is_not_followed(source):
    path = source/'frontend/miniprogram/external.js'; path.symlink_to('/outside-source')
    run_git(source, 'add', str(path)); run_git(source, 'commit', '-qm', 'symlink fixture')
    with pytest.raises(bundle.BundleError, match='regular files'):
        bundle.committed_files(source)


def test_explicit_historical_revision_does_not_change_checkout(source):
    old = run_git(source, 'rev-parse', 'HEAD').decode().strip()
    file = source/'backend/src/sales_backend/main.py'; before = file.read_bytes()
    file.write_bytes(b'# newer source\n'); run_git(source, 'add', str(file)); run_git(source, 'commit', '-qm', 'later revision')
    current = run_git(source, 'rev-parse', 'HEAD')
    revision, files, _ = bundle.committed_files(source, old)
    assert revision == old and files['backend/src/sales_backend/main.py'] == before
    assert run_git(source, 'rev-parse', 'HEAD') == current and file.read_bytes() == b'# newer source\n'


@pytest.mark.parametrize('url,profile', [
    ('https://user:password@example.test/api/v1', 'private'),
    ('https://example.test/api/v1?key=not-a-real-key', 'private'),
    ('http://example.test/api/v1', 'private'), ('https://example.test/api/v1', 'local'),
    ('http://127.0.0.1:18800/api/v1', 'private'), ('https://example.test/other', 'private'),
    ('https://example.test/api/\nv1', 'private'),
])
def test_target_is_explicit_and_profile_validated(source, url, profile):
    with pytest.raises(bundle.BundleError):
        prepare(source, api_url=url, profile=profile)


def test_local_target_and_project_check_agree(source, tmp_path):
    files, modes, _ = prepare(source, api_url='http://127.0.0.1:18800/api/v1', profile='local')
    archive = tmp_path/'local.zip'; digest = bundle.write_archive(files, modes, archive)
    actual, _ = bundle.read_verified(archive, digest)
    assert json.loads(actual['frontend/project.config.json'])['setting']['urlCheck'] is False


@pytest.mark.parametrize('kind', ['bytes', 'extra', 'missing', 'duplicate', 'symlink', 'traversal', 'fingerprint', 'frontend', 'malformed', 'deployment_claim'])
def test_artifact_tampering_is_rejected_with_reviewed_outer_digest(source, tmp_path, kind):
    files, _, manifest = prepare(source); files = dict(files)
    duplicate = None; mode = 0o100644
    if kind == 'bytes': files['backend/src/sales_backend/main.py'] += b'tampered'
    if kind == 'extra': files['backend/src/extra.py'] = b'extra'
    if kind == 'missing': del files['backend/src/sales_backend/main.py']
    if kind == 'duplicate': duplicate = 'backend/src/sales_backend/main.py'
    if kind == 'symlink': mode = 0o120777
    if kind == 'traversal': files['../outside'] = b'bad'
    if kind == 'fingerprint':
        manifest['dependency_locks'] = {}; files[bundle.MANIFEST] = bundle.json_bytes(manifest)
    if kind == 'frontend':
        name = 'frontend/miniprogram/config.js'; files[name] = b'API_BASE_URL: "https://wrong.example.test/api/v1"'
        manifest['file_hashes'][name] = bundle.sha256(files[name]); files[bundle.MANIFEST] = bundle.json_bytes(manifest)
    if kind == 'malformed': files[bundle.MANIFEST] = b'[]'
    if kind == 'deployment_claim':
        manifest['installation_verified'] = True; files[bundle.MANIFEST] = bundle.json_bytes(manifest)
    archive = tmp_path/'bad.zip'; digest = write_raw(archive, files, duplicate=duplicate, mode=mode)
    with pytest.raises(bundle.BundleError):
        bundle.read_verified(archive, digest)


def test_outer_digest_and_existing_output_are_enforced(source, tmp_path):
    files, modes, _ = prepare(source); archive = tmp_path/'candidate.zip'
    digest = bundle.write_archive(files, modes, archive)
    with pytest.raises(bundle.BundleError, match='digest'):
        bundle.read_verified(archive, '0'*64)
    with pytest.raises(bundle.BundleError, match='already exists'):
        bundle.write_archive(files, modes, archive)
    assert bundle.sha256(archive.read_bytes()) == digest
    assert not list(tmp_path.glob('.salesbuddy-bundle-*'))


def test_upgrade_plan_keeps_prior_migrations_and_is_not_installation_approval(source):
    _, _, before = prepare(source); after = copy.deepcopy(before)
    after['revision'] = 'b'*40; after['expected_schema'] = 'V166'
    after['migrations'].append({'key':'V166', 'path':'database/migrations/V166__new.sql', 'sha256':'c'*64})
    plan = bundle.upgrade_plan(before, after)
    assert plan['pending_migrations'] == ['V166'] and plan['ready_to_install'] is False
    assert plan['automatic_database_rollback'] is False
    for key in ('api_base_url', 'appid'):
        changed = copy.deepcopy(after); changed[key] += '-changed'
        with pytest.raises(bundle.BundleError, match='environment migration'):
            bundle.upgrade_plan(before, changed)
    for change in ('remove', 'replace', 'seed'):
        changed = copy.deepcopy(after)
        if change == 'remove': changed['migrations'].pop(0)
        if change == 'replace': changed['migrations'][0]['sha256'] = 'f'*64
        if change == 'seed': changed['migrations'].append({'key':'S002', 'path':'database/seeds/S002__new.sql', 'sha256':'d'*64})
        with pytest.raises(bundle.BundleError):
            bundle.upgrade_plan(before, changed)
