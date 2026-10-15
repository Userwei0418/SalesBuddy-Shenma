"""Recovery refuses incomplete/tampered/redirected archives before touching PostgreSQL."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/local'))
spec=importlib.util.spec_from_file_location('local_recovery',ROOT/'tools/local/recovery.py')
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)


def archive(tmp_path,monkeypatch):
    local=tmp_path/'state';folder=local/'backups'/'backup-20260926-010203-123456789abc'
    folder.mkdir(parents=True,mode=0o700)
    monkeypatch.setattr(recovery,'state_from_config',lambda cfg:local)
    cfg={'root':str(tmp_path/'source')}
    hashes={}
    for name in ('database.dump','settings.json','platform-mfa-keys.json'):
        path=folder/name;path.write_bytes(b'synthetic test archive');path.chmod(0o600)
        hashes[name]=recovery.digest(path)
    manifest={'kind':'salesbuddy-local-backup-v2','source_root':cfg['root'],'files':hashes}
    recovery.write_json(folder/'manifest.json',manifest,private=True)
    return cfg,folder,manifest


def test_valid_archive_and_corrupt_dump(tmp_path,monkeypatch):
    cfg,folder,manifest=archive(tmp_path,monkeypatch)
    assert recovery.validate_backup(cfg,folder)==manifest
    (folder/'database.dump').write_bytes(b'changed')
    with pytest.raises(recovery.RecoveryError,match='checksum'):recovery.validate_backup(cfg,folder)


@pytest.mark.parametrize('field,value', [('kind','foreign'),('source_root','/another/source')])
def test_foreign_identity_is_rejected(tmp_path,monkeypatch,field,value):
    cfg,folder,manifest=archive(tmp_path,monkeypatch);manifest[field]=value
    recovery.write_json(folder/'manifest.json',manifest,private=True)
    with pytest.raises(recovery.RecoveryError,match='identity'):recovery.validate_backup(cfg,folder)


def test_missing_keyring_and_unknown_member_path_are_rejected(tmp_path,monkeypatch):
    cfg,folder,manifest=archive(tmp_path,monkeypatch)
    del manifest['files']['platform-mfa-keys.json']
    recovery.write_json(folder/'manifest.json',manifest,private=True)
    with pytest.raises(recovery.RecoveryError,match='incomplete'):recovery.validate_backup(cfg,folder)
    manifest['files']['platform-mfa-keys.json']=recovery.digest(folder/'platform-mfa-keys.json')
    manifest['files']['../escape']='unused'
    recovery.write_json(folder/'manifest.json',manifest,private=True)
    with pytest.raises(recovery.RecoveryError,match='member path'):recovery.validate_backup(cfg,folder)


def test_archive_and_member_symlinks_are_rejected(tmp_path,monkeypatch):
    cfg,folder,manifest=archive(tmp_path,monkeypatch)
    alias=folder.parent/'backup-20260926-010203-abcdef123456';alias.symlink_to(folder,target_is_directory=True)
    with pytest.raises(recovery.RecoveryError,match='named backup'):recovery.validate_backup(cfg,alias)
    original=folder/'database.dump';content=original.read_bytes();original.unlink()
    outside=tmp_path/'outside';outside.write_bytes(content);outside.chmod(0o600);original.symlink_to(outside)
    with pytest.raises(recovery.RecoveryError):recovery.validate_backup(cfg,folder)


def test_readable_private_files_and_copies_are_refused(tmp_path,monkeypatch):
    cfg,folder,_=archive(tmp_path,monkeypatch)
    (folder/'settings.json').chmod(0o644)
    with pytest.raises(recovery.RecoveryError,match='private'):recovery.validate_backup(cfg,folder)
    destination=tmp_path/'copy'
    with pytest.raises(recovery.RecoveryError):recovery.copy_private(folder/'settings.json',destination)
    assert not destination.exists()


def test_copy_preserves_private_bytes_and_never_overwrites_existing_file(tmp_path):
    source=tmp_path/'secret';source.write_bytes(b'only a synthetic fixture');source.chmod(0o600)
    destination=tmp_path/'copy';recovery.copy_private(source,destination)
    assert destination.read_bytes()==source.read_bytes() and destination.stat().st_mode&0o777==0o600
    source.write_bytes(b'changed')
    with pytest.raises(FileExistsError):recovery.copy_private(source,destination)
    assert destination.read_bytes()==b'only a synthetic fixture'


@pytest.mark.asyncio
async def test_busy_restore_port_never_stops_or_replaces_existing_service(tmp_path,monkeypatch):
    cfg,folder,manifest=archive(tmp_path,monkeypatch)
    recovery.write_json(folder/'settings.json',cfg,private=True)
    manifest['files']['settings.json']=recovery.digest(folder/'settings.json')
    recovery.write_json(folder/'manifest.json',manifest,private=True)
    calls=[]
    monkeypatch.setattr(recovery,'pg_command',lambda *a,**k:calls.append(a))
    def busy(port):raise recovery.RecoveryError('port already in use')
    monkeypatch.setattr(recovery,'port_free',busy)
    with pytest.raises(recovery.RecoveryError,match='port already'):
        await recovery.restore_check(cfg,folder)
    assert not calls
    assert not list((tmp_path/'state/restore-drills').glob('*/owner-password'))
