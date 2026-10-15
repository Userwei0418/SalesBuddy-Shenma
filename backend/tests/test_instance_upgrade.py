"""Upgrade journal, candidate binding and interrupted filesystem transitions."""
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
import instance_upgrade as upgrade
from instance_files import InstanceError, atomic_json, read_private, runtime_path, source_check, no_pending_upgrade
from runtime_roles import load_policy, fingerprint

spec=importlib.util.spec_from_file_location('upgrade_fixture',Path(__file__).with_name('test_product_instance.py'))
fixtures=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
source,prepared=fixtures.source,fixtures.prepared
bundle=fixtures.bundle


def candidate(source,tmp_path,*,api_change=False):
    revision,files,modes=bundle.committed_files(source)
    policy=load_policy(); policy['schema']='V165'
    policy['migration_fingerprint']=fingerprint({r['key']:r['sha256'] for r in bundle.migration_manifest(files)})
    files['tools/installation/runtime_permissions.json']=bundle.json_bytes(policy)
    files['tools/installation/upgrade_contract.json']=bundle.json_bytes(upgrade.CONTRACT)
    if api_change: files['backend/openapi/openapi.yaml'] += b'\n# API compatibility review required\n'
    files,modes,_=bundle.prepare(files,modes,revision='a'*40,api_url='http://127.0.0.1:18810/api/v1',
        appid=fixtures.fixture_module.APPID,profile='local',product_name='upgrade-fixture')
    path=tmp_path/'incoming.zip'; digest=bundle.write_archive(files,modes,path)
    return path,digest


def test_pending_upgrade_binds_everything_except_reviewed_version_fields(prepared,source,tmp_path):
    path,digest=candidate(source,tmp_path)
    journal=upgrade.prepare(prepared.root,path,digest)
    actual,stage,packages=upgrade.load(prepared.root)
    assert journal==actual and packages['new'][1]['revision']=='a'*40
    assert journal['new']['database']==journal['old']['database']
    assert journal['new']['private_hashes']==journal['old']['private_hashes']
    with pytest.raises(InstanceError,match='pending'): no_pending_upgrade(prepared.root)
    with pytest.raises(InstanceError,match='pending'): upgrade.prepare(prepared.root,path,digest)
    assert not (stage/'runtime').exists()
    assert read_private(prepared.root/'INSTANCE.json') == journal['old']


@pytest.mark.parametrize('case',['database','credentials','runtime_path','root','plan','archive'])
def test_modified_upgrade_state_is_rejected(prepared,source,tmp_path,case):
    path,digest=candidate(source,tmp_path); journal=upgrade.prepare(prepared.root,path,digest)
    if case=='database': journal['new']['database']={**journal['new']['database'],'host':'external.example'}
    if case=='credentials': journal['new']['private_hashes']={}
    if case=='runtime_path': journal['new']['runtime_directory']='../../runtime'
    if case=='root': journal['root']=str(tmp_path)
    if case=='plan': journal['plan']['pending_migrations']=['V999']
    if case=='archive': (prepared.root/'upgrades'/journal['id']/'artifact.zip').write_bytes(b'changed')
    atomic_json(prepared.root/'run/upgrade.json',journal)
    with pytest.raises((InstanceError,ValueError)): upgrade.load(prepared.root)


def test_api_change_requires_explicit_compatibility_acknowledgement(prepared,source,tmp_path):
    path,digest=candidate(source,tmp_path,api_change=True)
    with pytest.raises(InstanceError,match='compatibility'): upgrade.prepare(prepared.root,path,digest)
    assert not (prepared.root/'run/upgrade.json').exists()
    assert upgrade.prepare(prepared.root,path,digest,acknowledge_api_change=True)['plan']['api_changed']


@pytest.mark.parametrize('phase',['old_release_saved','new_release_installed','old_archive_saved','new_archive_installed','metadata_switched'])
def test_half_switched_files_resume_without_overwriting_old_source(prepared,source,tmp_path,monkeypatch,phase):
    path,digest=candidate(source,tmp_path); journal=upgrade.prepare(prepared.root,path,digest)
    journal,stage,packages=upgrade.load(prepared.root)
    (stage/'runtime').mkdir(mode=0o700); (stage/'runtime/bin').mkdir(mode=0o700)
    (stage/'runtime/bin/python').touch(mode=0o600)
    original=upgrade.checkpoint
    def interrupted(root,value,at):
        original(root,value,at)
        if at==phase: raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(upgrade,'checkpoint',interrupted)
    with pytest.raises(RuntimeError): upgrade.switch_files(prepared.root,journal,stage,packages)
    monkeypatch.setattr(upgrade,'checkpoint',original)
    journal,stage,packages=upgrade.load(prepared.root)
    upgrade.switch_files(prepared.root,journal,stage,packages)
    assert source_check(prepared.root)[0]==journal['new']
    assert runtime_path(prepared.root)==stage/'runtime'
    upgrade.verify_tree(stage/'previous-release',*packages['old'])
    receipt=upgrade.finish(prepared.root,journal,stage,'completed')
    assert receipt['restart_required'] and not receipt['services_started']
    assert not (prepared.root/'run/upgrade.json').exists()
    assert (stage/'completed.json').exists()


def test_unexpected_source_at_switch_is_retained_and_refused(prepared,source,tmp_path):
    path,digest=candidate(source,tmp_path); upgrade.prepare(prepared.root,path,digest)
    journal,stage,packages=upgrade.load(prepared.root)
    unexpected=prepared.root/'release/unexpected.txt'; unexpected.write_text('retain me'); unexpected.chmod(0o600)
    with pytest.raises(InstanceError,match='differs'): upgrade.switch_files(prepared.root,journal,stage,packages)
    assert unexpected.read_text()=='retain me' and not (stage/'previous-release').exists()


def test_old_uv_runtime_is_accepted_but_shared_writable_runtime_is_not(prepared):
    runtime=prepared.root/'runtime'; runtime.mkdir(mode=0o755)
    assert runtime_path(prepared.root)==runtime
    runtime.chmod(0o777)
    with pytest.raises(InstanceError,match='another user'): runtime_path(prepared.root)


def test_dependency_install_does_not_follow_redirected_runtime(prepared,source,tmp_path,monkeypatch):
    path,digest=candidate(source,tmp_path); upgrade.prepare(prepared.root,path,digest)
    _,stage,_=upgrade.load(prepared.root)
    outside=tmp_path/'outside-runtime'; outside.mkdir(mode=0o700)
    (stage/'runtime').symlink_to(outside)
    monkeypatch.setattr(upgrade,'run_private',lambda *args,**kwargs: pytest.fail('must not invoke uv against redirected runtime'))
    with pytest.raises(InstanceError,match='redirected'): upgrade.install_dependencies(prepared.root,stage)
