"""Local runtime relocation keeps data, rejects ambiguous paths and stops before copying."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec=importlib.util.spec_from_file_location(name, ROOT / 'tools/local' / (name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# dev's existing test/entrypoint installs its sibling tools directory for imports.
import sys
sys.path.insert(0,str(ROOT/'tools/local'))
paths=load('local_paths')
move=load('relocate')


def test_state_is_outside_documents_and_namespaced_by_canonical_checkout(tmp_path):
    repo=tmp_path/'Documents/source';home=tmp_path/'user'
    mac=paths.state_directory(repo,home=home,system='darwin')
    assert mac.parent==home/'Library/Application Support/Raccoon SalesBuddy'
    assert not mac.is_relative_to(repo)
    assert mac==paths.state_directory(repo/'../source',home=home,system='darwin')
    assert mac!=paths.state_directory(repo/'other',home=home,system='darwin')
    assert paths.state_directory(repo,home=home,system='linux').parent==home/'.local/share/raccoon-salesbuddy'


def test_configuration_cannot_redirect_to_arbitrary_or_legacy_state(tmp_path,monkeypatch):
    expected=tmp_path/'state'
    monkeypatch.setattr(paths,'state_directory',lambda root:expected)
    cfg=dict(kind='salesbuddy-local-v1',root=str(tmp_path/'source'),state_root=str(expected))
    assert paths.state_from_config(cfg)==expected
    for change in ({'state_root':'/var/lib/postgresql'},{'root':'relative'},{'kind':'production'},{'state_root':None}):
        with pytest.raises(RuntimeError):paths.state_from_config({**cfg,**change})
    other=tmp_path/'other';other.mkdir();expected.symlink_to(other,target_is_directory=True)
    with pytest.raises(RuntimeError):paths.state_from_config(cfg)


def test_source_move_keeps_existing_state_for_both_entries(tmp_path):
    old=tmp_path/'Documents/source';old.mkdir(parents=True)
    home=tmp_path/'user'
    expected=paths.state_directory(old,home=home,system='darwin')
    new=tmp_path/'Developer/source';new.parent.mkdir()
    old.rename(new);old.symlink_to(new,target_is_directory=True)
    (new/'.git').mkdir()
    receipt=new/'.git/local-source-relocation.json'
    receipt.write_text(json.dumps(dict(kind='salesbuddy-local-source-relocation-v1',source=str(old),target=str(new))))
    receipt.chmod(0o600)
    assert paths.state_directory(new,home=home,system='darwin')==expected
    assert paths.state_directory(old,home=home,system='darwin')==expected
    # A copied receipt, insecure receipt, or redirected old entry never selects
    # the previous checkout's runtime.
    receipt.chmod(0o644)
    with pytest.raises(RuntimeError,match='private file'):paths.state_directory(new,home=home)
    receipt.chmod(0o600)
    old.unlink();old.mkdir()
    with pytest.raises(RuntimeError,match='does not match'):paths.state_directory(new,home=home)
    old.rmdir();old.symlink_to(new,target_is_directory=True)
    data=json.loads(receipt.read_text());data['target']=str(tmp_path/'other')
    receipt.write_text(json.dumps(data))
    with pytest.raises(RuntimeError,match='does not match'):paths.state_directory(new,home=home)


def test_source_receipt_symlink_cannot_redirect_runtime(tmp_path):
    repo=tmp_path/'source';(repo/'.git').mkdir(parents=True)
    (repo/'.git/local-source-relocation.json').symlink_to(tmp_path/'missing')
    with pytest.raises(RuntimeError,match='private file'):paths.state_directory(repo,home=tmp_path)


def fixture(tmp_path,monkeypatch,*,running=False,clean=True):
    source=tmp_path/'source';old=source/'.local';old.mkdir(parents=True)
    pg=old/'postgres';pg.mkdir();(pg/'PG_VERSION').write_text('16\n');(pg/'data').write_bytes(b'preserve database')
    (old/'platform-mfa-keys.json').write_text('private synthetic keyring')
    (old/'materials').mkdir();(old/'materials/audio.wav').write_bytes(b'original')
    (old/'custom-note.txt').write_text('keep user additions')
    (old/'builds').mkdir();(old/'builds/cache').write_text('rebuild')
    cfg=dict(kind='salesbuddy-local-v1',root=str(source),db_port=15432,api_port=18800,test_password='synthetic')
    (old/'settings.json').write_text(json.dumps(cfg))
    target=tmp_path/'local-state'/'repo-id';calls=[]
    if running:(pg/'postmaster.pid').write_text('123\n'+str(pg)+'\n0\n15432\n')
    def subprocess_run(args,**kwargs):
        if args[-1]=='status':return SimpleNamespace(returncode=0 if running else 3)
        return SimpleNamespace(stdout='Database cluster state: '+('shut down' if clean else 'in production')+'\n')
    monkeypatch.setattr(move.subprocess,'run',subprocess_run)
    def run(args,**kwargs):
        calls.append('pg-stop');(pg/'postmaster.pid').unlink(missing_ok=True)
    def write(path,body,private=False):
        path.write_text(json.dumps(body));path.chmod(0o600 if private else 0o644)
    dev=SimpleNamespace(ROOT=source,LOCAL=target,DB_PORT=15432,API_PORT=18800,pg_bin=lambda:Path('/unused'),
        clean_env=lambda:{},stop_apps=lambda folder:calls.append('apps-stop'),run=run,write_json=write)
    return dev,old,target,calls


def test_relocation_preserves_database_keys_originals_and_user_additions(tmp_path,monkeypatch):
    dev,old,target,calls=fixture(tmp_path,monkeypatch,running=True)
    before=move.files(old)
    move.relocate(dev)
    assert calls==['apps-stop','pg-stop']
    for name in ('postgres/data','platform-mfa-keys.json','materials/audio.wav','custom-note.txt'):
        assert (target/name).read_bytes()==(old/name).read_bytes()
    assert not (target/'builds').exists()
    assert json.loads((target/'settings.json').read_text())['state_root']==str(target)
    assert json.loads((old/'settings.json').read_text()).get('state_root') is None
    assert json.loads((target/'relocation.json').read_text())['source_retained']
    assert (target/'settings.json').stat().st_mode&0o777==0o600
    assert before['postgres/data']==move.files(old)['postgres/data']
    with pytest.raises(RuntimeError,match='Destination exists'):move.relocate(dev)


def test_unclean_shutdown_or_symlink_never_creates_accepted_destination(tmp_path,monkeypatch):
    dev,old,target,_=fixture(tmp_path,monkeypatch,clean=False)
    with pytest.raises(RuntimeError,match='not clean'):move.relocate(dev)
    assert not target.exists()
    monkeypatch.setattr(move.subprocess,'run',lambda args,**kwargs: SimpleNamespace(returncode=3,stdout='Database cluster state: shut down\n'))
    (old/'materials/escape').symlink_to(tmp_path)
    with pytest.raises(RuntimeError,match='symlink'):move.relocate(dev)
    assert not target.exists() and (old/'postgres/data').is_file()


def test_existing_destination_and_wrong_legacy_identity_are_never_overwritten(tmp_path,monkeypatch):
    dev,old,target,calls=fixture(tmp_path,monkeypatch)
    target.mkdir(parents=True);(target/'mine').write_text('retained')
    with pytest.raises(RuntimeError,match='Destination exists'):move.relocate(dev)
    assert not calls and (target/'mine').read_text()=='retained'
    cfg=json.loads((old/'settings.json').read_text());cfg['root']='/another/checkout';(old/'settings.json').write_text(json.dumps(cfg))
    with pytest.raises(RuntimeError,match='identity mismatch'):move.relocate(dev)
    assert not calls
