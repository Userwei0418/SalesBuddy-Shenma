"""Reject incomplete/redirected backups without reading or replacing user data."""
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/installation'))
from instance_backup import copy_file, material_tree, validate_backup, digest
from instance_files import InstanceError, atomic_json, read_private

spec = importlib.util.spec_from_file_location('backup_instance_fixture', Path(__file__).with_name('test_product_instance.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
source, prepared = fixtures.source, fixtures.prepared


@pytest.fixture
def saved(prepared):
    root = prepared.root
    parent = root/'backups'; parent.mkdir(mode=0o700)
    directory = parent/'backup-20260926-120000-123456abcdef'; directory.mkdir(mode=0o700)
    (root/'materials/BACKUP.json').write_bytes(b'synthetic original with a reserved-looking name')
    data = read_private(root/'INSTANCE.json')
    for name in ('INSTANCE.json','artifact.zip', *('private/'+n for n in data['private_hashes']), 'materials/BACKUP.json'):
        copy_file(root/name, directory/name, private=not name.startswith('materials/'))
    (directory/'database.dump').touch(mode=0o600)
    manifest = {'format':'salesbuddy-instance-backup-v1','source_root':str(root),'instance_id':data['instance_id'],
        'revision':data['revision'],'files':{str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()}}
    atomic_json(directory/'BACKUP.json', manifest)
    return root, directory


def test_private_manifest_accepts_complete_members_including_original_named_backup(saved):
    root, directory = saved
    manifest, _ = validate_backup(root, directory)
    assert 'materials/BACKUP.json' in manifest['files']
    assert (directory/'materials/BACKUP.json').stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('case', ['missing', 'extra', 'changed', 'symlink', 'public', 'identity', 'traversal', 'endpoint'])
def test_invalid_backup_refused_before_restore(saved, case):
    root, directory = saved
    if case == 'missing': (directory/'database.dump').unlink()
    if case == 'extra': (directory/'unexpected').touch(mode=0o600)
    if case == 'changed': (directory/'database.dump').write_bytes(b'changed')
    if case == 'symlink':
        (directory/'database.dump').unlink(); (directory/'database.dump').symlink_to(root/'artifact.zip')
    if case == 'public': (directory/'database.dump').chmod(0o644)
    if case == 'endpoint':
        saved = read_private(directory/'INSTANCE.json')
        saved['database']['host'] = 'untrusted.example.test'
        atomic_json(directory/'INSTANCE.json', saved)
        manifest = read_private(directory/'BACKUP.json')
        manifest['files']['INSTANCE.json'] = digest(directory/'INSTANCE.json')
        atomic_json(directory/'BACKUP.json', manifest)
    if case in ('identity','traversal'):
        value = read_private(directory/'BACKUP.json')
        if case == 'identity': value['instance_id'] = 'different'
        else: value['files']['../../outside'] = '0'*64
        atomic_json(directory/'BACKUP.json', value)
    with pytest.raises((InstanceError, OSError)): validate_backup(root, directory)


def test_material_tree_rejects_symlink_even_inside_private_root(prepared):
    root = prepared.root
    (root/'materials/redirect').symlink_to(root/'INSTANCE.json')
    with pytest.raises(InstanceError, match='redirected'): material_tree(root)


def test_backup_copy_is_exclusive_and_keeps_existing_data(tmp_path):
    source = tmp_path.resolve()/'source'; source.touch(mode=0o600); source.write_bytes(b'first')
    target = tmp_path.resolve()/'target'
    copy_file(source, target)
    source.write_bytes(b'second')
    with pytest.raises(FileExistsError): copy_file(source, target)
    assert target.read_bytes() == b'first'
