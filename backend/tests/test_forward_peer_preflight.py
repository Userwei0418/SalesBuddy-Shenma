"""Local filesystem and process fixtures only; no host, SQL, or service mutation."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/'deploy'))
import release_forward_bundle as forward


def ref(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


@pytest.fixture
def peer(tmp_path, monkeypatch):
    monkeypatch.setattr(forward.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(forward.os, 'chown', lambda *_: None)
    root=tmp_path/'app'; release=root/'releases/candidate'
    (release/'database/scripts').mkdir(parents=True)
    (release/'database/scripts/migrate.py').write_text('# synthetic readable migrator')
    sql=release/'database/migrations/V256__test.sql'; sql.parent.mkdir()
    sql.write_text('SELECT 1;')
    helper=release/'backend/deploy/acl.py'; helper.parent.mkdir(parents=True)
    helper.write_text('# synthetic readable ACL helper')
    policy=ref(tmp_path/'policy.json', {'synthetic':True})
    acl=ref(tmp_path/'acl.json', {'policy_ref':policy,'source_files':{str(helper):forward.file_ref(helper)['sha256']}})
    plan={
        'backend':'candidate', 'acl_plan':acl, 'acl_source':forward.file_ref(helper),
        'validation':ref(tmp_path/'validation.json', {'status':'passed'}),
        'migration_plan':ref(tmp_path/'migration.json', [{
            'key':'V256','file':'migrations/V256__test.sql','sha256':forward.file_ref(sql)['sha256']}]),
    }
    owned=tmp_path/'owned'; owned.mkdir()
    class Host:
        def __init__(self):
            self.root=root; self.commands=[]; self.probe_refs=None; self.before_probe=None
        def run(self, args):
            self.commands.append([str(x) for x in args])
            assert args[:4]==['runuser','-u','postgres','--']
            if args[4]=='test':
                assert os.access(args[-1],os.X_OK)
                return ''
            assert args[5]=='-c'
            self.probe_refs=json.loads(args[7])
            if self.before_probe:
                self.before_probe(dict(self.probe_refs))
            # Execute the exact peer probe code locally; only runuser/OS identity is substituted.
            done=subprocess.run([sys.executable,'-c',args[6],args[7]],text=True,capture_output=True,check=True)
            return done.stdout.strip()
    return Host(),plan,owned


def test_every_child_reference_is_hashed_in_peer_context(peer):
    host,plan,owned=peer
    forward.peer_access(host,plan,owned)
    refs=dict(host.probe_refs)
    assert set(refs)=={'migration_plan','validation','acl_source','acl_plan','acl_policy',
                      'acl_source_0','migrator','migration_V256'}
    assert refs['migration_plan']==plan['migration_plan']
    assert refs['validation']==plan['validation']
    assert (owned.stat().st_mode & 0o777)==0o710
    assert all(cmd[4] in ('test',str(host.root/'releases/candidate/.venv/bin/python')) for cmd in host.commands)


@pytest.mark.parametrize('label',['migration_plan','validation','acl_policy','acl_source_0','migrator','migration_V256'])
@pytest.mark.parametrize('fault',['unreadable','changed'])
def test_peer_only_unreadable_or_changed_input_is_rejected(peer,label,fault):
    host,plan,owned=peer
    def inject(refs):
        path=Path(refs[label]['path'])
        if fault=='unreadable': path.unlink()
        else: path.write_text('changed after root read')
    host.before_probe=inject
    # A duplicated helper ref is detected at its first stable label.
    expected='acl_source' if label=='acl_source_0' else label
    with pytest.raises(forward.Refused,match='Database peer input unreadable or changed: '+expected):
        forward.peer_access(host,plan,owned)


def test_missing_reference_refuses_before_any_peer_command(peer):
    host,plan,owned=peer
    Path(plan['validation']['path']).unlink()
    with pytest.raises(forward.Refused,match='Regular absolute file required'):
        forward.peer_access(host,plan,owned)
    assert host.commands==[]


@pytest.mark.parametrize('receipt,returncode,match',[
    (None,1,'exited with code 1 without a DATABASE.json receipt'),
    (None,0,'exited with code 0 without a DATABASE.json receipt'),
    ('bad-json',1,'unreadable or malformed'),
    ([],0,'must be an object'),
    ('symlink',1,'without a DATABASE.json receipt'),
])
def test_missing_or_invalid_database_receipt_preserves_child_outcome(peer,tmp_path,monkeypatch,receipt,returncode,match):
    host,plan,owned=peer
    binding=ref(tmp_path/'PLAN.json',plan)
    folder=owned/'database'
    class Child:
        pid=12345
        def wait(self,timeout): return returncode
        def poll(self): return returncode
    def launch(*args,**kwargs):
        if receipt=='symlink':
            (folder/'DATABASE.json').symlink_to(tmp_path/'PLAN.json')
        elif receipt is not None:
            (folder/'DATABASE.json').write_text(receipt if isinstance(receipt,str) else json.dumps(receipt))
        return Child()
    monkeypatch.setattr(forward.subprocess,'Popen',launch)
    with pytest.raises(forward.Refused,match=match):
        forward.peer_phase(host,binding,folder)
    observed=json.loads((folder/'PEER_PROCESS.json').read_text())
    assert observed['returncode']==returncode
    assert observed['database_receipt_present'] is (receipt is not None and receipt!='symlink')
    assert json.loads((folder/'PLAN.json').read_text())==plan


@pytest.mark.parametrize('returncode',[0,1])
def test_existing_database_receipt_is_returned_without_forging_success(peer,tmp_path,monkeypatch,returncode):
    host,plan,owned=peer
    binding=ref(tmp_path/'PLAN.json',plan); folder=owned/'database'
    receipt={'success':returncode==0,'closed':True,'errors':[] if returncode==0 else [{'type':'FixtureFailure'}]}
    class Child:
        pid=12345
        def wait(self,timeout): return returncode
        def poll(self): return returncode
    def launch(*args,**kwargs):
        (folder/'DATABASE.json').write_text(json.dumps(receipt))
        return Child()
    monkeypatch.setattr(forward.subprocess,'Popen',launch)
    observed=forward.peer_phase(host,binding,folder)
    assert observed['returncode']==returncode and observed['result']==receipt
    assert observed['receipt']==forward.file_ref(folder/'DATABASE.json')


def test_peer_ref_failure_prevents_forward_stop_backup_and_database_launch(peer,tmp_path,monkeypatch):
    from tests.test_release_bundle_control import FixtureHost
    peer_host,p,unused=peer
    host=FixtureHost(tmp_path/'business')
    release=host.root/'releases/new'
    (release/'database/scripts').mkdir(parents=True)
    (release/'database/scripts/migrate.py').write_text('# synthetic readable migrator')
    sql=release/'database/migrations/V256__test.sql';sql.parent.mkdir()
    sql.write_text('SELECT 1;')
    prior={'status':'passed','tested_mini_versions':['1.1.1'],
        'tested_source_sha256':host.artifacts('old','old')['backend']['sha256']}
    old=forward.original.make_bundle(host,'old','old','old','1.1.1',['1.1.1'],prior)
    host.record_current(old)
    ledger={'registered':['V202'],'checksums':{'V202':'a'*64},'running_jobs':0}
    host.database_raw=lambda:ledger.copy()
    host.run=peer_host.run
    p.update(bundle_id='peer-rejection',target_schema='V203',backend='new',web='new',expected_backend='old',expected_web='old',
        expected_database=host.database(),expected_ledger=ledger,expected_current_bundle={'bundle_id':'old'},
        active_mini_versions=['1.1.1'],supported_mini_versions=['1.1.1'],candidate=host.artifacts('new','new'))
    binding=ref(tmp_path/'forward-plan.json',p)
    monkeypatch.setattr(forward,'inputs',lambda _: (p,[],prior))
    monkeypatch.setattr(forward,'source_plan',lambda *_:None)
    peer_host.before_probe=lambda refs:Path(refs['validation']['path']).unlink()
    def forbidden_database(*args):
        raise AssertionError('Pre-stop validation failure must never dispatch the database phase')
    with pytest.raises(forward.Refused,match='Database peer input unreadable or changed: validation'):
        forward.forward(host,binding,database_phase=forbidden_database)
    assert not any(event in host.events for event in ('stop','start','backup'))
    assert not (host.state/'TRANSACTION.json').exists()
    assert forward.original.pointer(host.root)==forward.original.pointer(host.web)=='old'
