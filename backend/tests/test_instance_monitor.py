"""Read-only diagnosis uses only synthetic private instances and loopback servers."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

SOURCE = Path(os.environ.get('SALESBUDDY_SOURCE_ROOT', Path(__file__).resolve().parents[2]))
sys.path.append(str(SOURCE/'tools/installation'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
import instance_monitor as monitor
import instance_process
from instance_files import InstanceError, atomic_json, read_private
from instance_backup import digest

spec = importlib.util.spec_from_file_location('monitor_backup_fixture', SOURCE/'backend/tests/test_instance_backup.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
source, prepared, saved = fixture.source, fixture.prepared, fixture.saved

POLICY = {'feishu_required':False,'verify_backup':False,'disk_min_free_bytes':0,
          'disk_max_used_percent':100,'backup_max_age_seconds':86400,'http_timeout_seconds':.2}


def ready_root(prepared):
    root = prepared.root; data = read_private(root/'INSTANCE.json')
    atomic_json(root/'run/initialized.json', {'instance_id':data['instance_id'],'revision':data['revision']})
    return root,data


def record(root, data, service='api'):
    return {'pid':1122,'started':'Thu Jan 1 00:00:00 2026','instance_id':data['instance_id'],'service':service,
        'command':str(root/'runtime/bin/python')+' '+str(root/'release/tools/installation/instance_service.py')+
        ' --root '+str(root)+' --service '+service+' --instance-id '+data['instance_id']}


def raw_snapshot(status='fail',code='stopped',now=100):
    return {'format':monitor.FORMAT,'sampled_at':now,'instance_id':'synthetic',
            'checks':{'process.api':monitor.finding(status,code)},'policy':deepcopy(POLICY)}


def test_stopped_services_still_write_snapshot_without_reading_database(prepared,monkeypatch):
    root,_ = ready_root(prepared)
    monkeypatch.setenv('SALESBUDDY_INSTALL_DB_PASSWORD','do-not-read')
    monkeypatch.setattr(monitor,'health_json',lambda *a,**k: pytest.fail('API must not be called without identity'))
    snapshot = monitor.run(root,POLICY)
    assert snapshot['status'] == 'fail'
    assert snapshot['checks']['process.api']['code'] == 'stopped'
    assert snapshot['checks']['process.worker']['code'] == 'stopped'
    assert snapshot['checks']['database']['status'] == 'unknown'
    assert 'do-not-read' not in json.dumps(snapshot)
    assert not (root/'run/api.json').exists()
    assert (root/'run/monitor/state.json').stat().st_mode & 0o077 == 0
    assert (root/'run/monitor').stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('change',['gone','pid_reused','command_replaced','record_other_instance','forged_command'])
def test_process_identity_never_adopts_a_reused_or_unrelated_pid(prepared,monkeypatch,change):
    root,data = ready_root(prepared); entry = record(root,data)
    if change == 'record_other_instance': entry['instance_id'] = 'other'
    if change == 'forged_command': entry['command'] = '/bin/sleep 100'
    atomic_json(root/'run/api.json',entry)
    live = {key:entry[key] for key in ('pid','command','started')}
    if change == 'gone': live = None
    if change == 'pid_reused': live['started'] = 'Fri Jan 2 00:00:00 2026'
    if change == 'command_replaced': live['command'] = '/bin/sleep 200'
    monkeypatch.setattr(instance_process,'process_identity',lambda pid:live)
    value,observed = monitor.process(root,data,'api')
    assert observed is None and value['status'] in {'fail','unknown'}


def responses(data, *, database=True,model=True,auth=True,revision=None):
    checks={'database':database,'auth_configured':auth,'model_gateway_configured':model}
    healthy = all(checks.values())
    return {'live':(200,{'status':'ok'}),'version':(200,{'revision':revision or data['revision'],
            'expected_schema':data['expected_schema']}),'ready':(200 if healthy else 503,
             {'status':'ok' if healthy else 'degraded','checks':checks})}


@pytest.mark.parametrize('case',['model','database','auth','wrong_revision','bad_type','other_listener','changed_process'])
def test_readiness_separates_database_auth_model_and_version(prepared,monkeypatch,case):
    root,data = ready_root(prepared); entry = record(root,data)
    replies=responses(data, database=case!='database',model=case!='model',auth=case!='auth',
                      revision='f'*40 if case=='wrong_revision' else None)
    if case=='bad_type': replies['ready'][1]['checks']['database']='true'
    monkeypatch.setattr(monitor,'listener_owned',lambda *a:case!='other_listener')
    monkeypatch.setattr(monitor,'process',lambda *a:(monitor.finding('pass','matched'),None if case=='changed_process' else entry))
    monkeypatch.setattr(monitor,'health_json',lambda port,endpoint,**kw:replies[endpoint])
    checks=monitor.api_checks(root,data,entry,.2)
    if case=='model': assert checks['database']['status']=='pass' and checks['model']['code']=='not_configured'
    if case=='database': assert checks['database']['status']=='fail' and checks['model']['status']=='pass'
    if case=='auth': assert checks['authentication']['status']=='fail'
    if case=='wrong_revision': assert checks['api.version']['status']=='fail' and checks['database']['status']=='unknown'
    if case=='bad_type': assert checks['database']['code']=='invalid_readiness'
    if case in {'other_listener','changed_process'}: assert checks['api.live']['status']=='fail' and checks['database']['status']=='unknown'


@pytest.mark.parametrize('case',['low_bytes','high_percent','unavailable','invalid_stats'])
def test_disk_thresholds_and_unknown_statistics(tmp_path,monkeypatch,case):
    values=(100,50,50)
    if case=='invalid_stats': values=(0,0,0)
    def usage(_):
        if case=='unavailable': raise OSError('synthetic failure')
        return values
    monkeypatch.setattr(monitor.shutil,'disk_usage',usage)
    policy={**POLICY,'disk_min_free_bytes':60 if case=='low_bytes' else 0,
            'disk_max_used_percent':40 if case=='high_percent' else 100}
    result=monitor.disk_check(tmp_path,policy)
    assert result['status']==('fail' if case in {'low_bytes','high_percent'} else 'unknown')


def save_time(directory,now):
    manifest=read_private(directory/'BACKUP.json')
    manifest['created_at']=datetime.fromtimestamp(now,tz=timezone.utc).isoformat()
    atomic_json(directory/'BACKUP.json',manifest)


@pytest.mark.parametrize('case',['unknown','verified','expired','future','incomplete','changed','symlink','identity','bad_time'])
def test_backup_validation_and_age(saved,case):
    root,directory=saved; data=read_private(root/'INSTANCE.json'); now=1800000000
    save_time(directory,now-(100000 if case=='expired' else -100 if case=='future' else 10))
    if case=='incomplete': (directory/'BACKUP.json').unlink()
    if case=='changed': (directory/'database.dump').write_bytes(b'synthetic corruption')
    if case=='symlink':
        (directory/'database.dump').unlink(); (directory/'database.dump').symlink_to(root/'artifact.zip')
    if case in {'identity','bad_time'}:
        value=read_private(directory/'BACKUP.json')
        value['instance_id' if case=='identity' else 'created_at']='wrong'
        atomic_json(directory/'BACKUP.json',value)
    checks=monitor.backup_checks(root,data,{**POLICY,'verify_backup':case!='unknown'},now)
    if case=='unknown': assert checks['backup.integrity']['code']=='not_verified'
    if case=='verified': assert checks['backup.integrity']['status']=='pass'
    if case=='expired': assert checks['backup']['code']=='expired'
    if case=='future': assert checks['backup']['status']=='unknown'
    if case=='incomplete': assert checks['backup.incomplete']['status']=='degraded'
    if case in {'changed','symlink'}: assert checks['backup.integrity']['status']=='fail'
    if case in {'identity','bad_time'}: assert checks['backup']['status']=='fail'


@pytest.mark.parametrize('case',['correct','wrong_digest','wrong_path','bad_json','corrupt_archive'])
def test_restore_receipt_requires_current_manifest_digest(saved,case):
    root,directory=saved; data=read_private(root/'INSTANCE.json'); now=1800000000; save_time(directory,now-10)
    receipt={'status':'passed','backup':str(directory),'backup_manifest_sha256':digest(directory/'BACKUP.json'),
             'revision':data['revision'],'probe_database_removed':True}
    if case=='wrong_digest': receipt['backup_manifest_sha256']='f'*64
    if case=='wrong_path': receipt['backup']='/another/backup'
    atomic_json(root/'run/last-restore-check.json',receipt)
    if case=='bad_json': (root/'run/last-restore-check.json').write_bytes(b'{invalid')
    if case=='corrupt_archive': (directory/'database.dump').write_bytes(b'bad')
    checks=monitor.backup_checks(root,data,{**POLICY,'verify_backup':True},now)
    assert checks['backup.restore']['status']==('pass' if case=='correct' else 'unknown')


def test_events_are_deduplicated_recovered_and_bounded(tmp_path):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    first=monitor.save_sample(directory,raw_snapshot(),None)
    same=monitor.save_sample(directory,raw_snapshot(now=101),first)
    assert len(same['events'])==1
    changed=monitor.save_sample(directory,raw_snapshot('unknown','unavailable',102),same)
    recovered=monitor.save_sample(directory,raw_snapshot('pass','matched',103),changed)
    assert len(recovered['events'])==3 and recovered['events'][-1]['kind']=='recovered'
    previous=recovered
    for n in range(205): previous=monitor.save_sample(directory,raw_snapshot('fail' if n%2 else 'pass',now=104+n),previous)
    assert len(previous['events'])==monitor.EVENT_LIMIT
    assert monitor.load_previous(directory)==previous


def test_clock_rollback_is_unknown_and_does_not_erase_prior_incidents(tmp_path):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    first=monitor.save_sample(directory,raw_snapshot(now=1000),None)
    backwards=monitor.save_sample(directory,raw_snapshot(now=900),first)
    assert backwards['checks']['clock']['status']=='unknown'
    assert backwards['sequence']==2 and backwards['events'][0]==first['events'][0]


def test_unverified_backup_keeps_overall_status_unknown(tmp_path):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    current=raw_snapshot('pass','matched')
    current['checks'].update({'backup':monitor.finding('pass','recent'),
                              'backup.integrity':monitor.finding('unknown','not_verified')})
    result=monitor.save_sample(directory,current,None)
    assert result['status']=='unknown'
    assert result['events'][0]['to']=='unknown'


@pytest.mark.parametrize('change',['events','status','sequence','timestamp'])
def test_structurally_corrupt_state_is_not_silently_overwritten(tmp_path,change):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    value=monitor.save_sample(directory,raw_snapshot(),None)
    if change=='events': value['events']=[{'unexpected':'broken'}]
    if change=='status': value['checks']['process.api']['status']='healthy-ish'
    if change=='sequence': value['sequence']='one'
    if change=='timestamp': value['sampled_at']=float('nan')
    atomic_json(directory/'state.json',value)
    before=(directory/'state.json').read_bytes()
    with pytest.raises(InstanceError): monitor.load_previous(directory)
    assert (directory/'state.json').read_bytes()==before


def test_pending_maintenance_and_wrong_initialized_revision_are_not_healthy(prepared):
    root,data=ready_root(prepared)
    atomic_json(root/'run/initialized.json',{'instance_id':data['instance_id'],'revision':'f'*40})
    value=monitor.sample(root,POLICY)
    assert value['checks']['instance']['code']=='initialization_mismatch'
    assert value['checks']['database']['status']=='unknown'
    atomic_json(root/'run/upgrade.json',{'synthetic':'pending'})
    value=monitor.sample(root,POLICY)
    assert value['checks']['instance']['code']=='maintenance_pending'
    assert value['checks']['database']['status']=='unknown'


def test_state_corruption_and_write_failure_preserve_previous_bytes(tmp_path,monkeypatch):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    first=monitor.save_sample(directory,raw_snapshot(),None); before=(directory/'state.json').read_bytes()
    def failing(*a,**k): raise OSError('synthetic disk full')
    monkeypatch.setattr(monitor,'atomic_json',failing)
    with pytest.raises(OSError): monitor.save_sample(directory,raw_snapshot('pass','matched',101),first)
    assert (directory/'state.json').read_bytes()==before
    (directory/'state.json').write_bytes(b'{invalid')
    with pytest.raises(ValueError): monitor.load_previous(directory)


def test_one_observer_and_private_state_paths(prepared):
    root,_=ready_root(prepared)
    with monitor.observer(root):
        with pytest.raises(InstanceError,match='Another observer'):
            with monitor.observer(root): pytest.fail('lock admitted another observer')
    (root/'run/monitor/state.json').symlink_to(root/'INSTANCE.json')
    with pytest.raises(OSError): monitor.load_previous(root/'run/monitor')


def test_watch_honors_sample_limit_without_starting_or_scheduling_services(prepared,monkeypatch):
    root,_=ready_root(prepared); sleeps=[]
    monkeypatch.setattr(monitor.time,'sleep',sleeps.append)
    result=monitor.run(root,POLICY,interval=1,max_samples=3)
    assert result['sequence']==3 and len(sleeps)==2
    assert not (root/'run/api.json').exists()


@pytest.mark.parametrize('after',['pass','fail'])
def test_failure_unobservable_observed_chain_preserves_recovery_and_incident_identity(prepared,monkeypatch,after):
    root,data=ready_root(prepared); outcome={'status':'fail'}
    monkeypatch.setattr(monitor,'api_checks',lambda *args:{'api.live':
        monitor.finding(outcome['status'],'alive' if outcome['status']=='pass' else 'health_unavailable_or_invalid')})
    directory=root/'run/monitor'; directory.mkdir(mode=0o700)
    first=monitor.save_sample(directory,monitor.sample(root,POLICY,now=100),None)
    atomic_json(root/'run/initialized.json',{'instance_id':data['instance_id'],'revision':'f'*40})
    unknown=monitor.save_sample(directory,monitor.sample(root,POLICY,now=101),first)
    repeated=monitor.save_sample(directory,monitor.sample(root,POLICY,now=102),unknown)
    assert unknown['checks']['api.live']==monitor.finding('unknown','not_observed')
    assert repeated['events']==unknown['events']
    atomic_json(root/'run/initialized.json',{'instance_id':data['instance_id'],'revision':data['revision']})
    outcome['status']=after
    observed=monitor.save_sample(directory,monitor.sample(root,POLICY,now=103),repeated)
    events=[event for event in observed['events'] if event['check']=='api.live']
    assert [event['to'] for event in events]==['fail','unknown',after]
    assert [event['kind'] for event in events]==['first_failure','changed','recovered' if after=='pass' else 'changed']


def test_disappearing_optional_check_is_kept_unknown_instead_of_forgotten(tmp_path):
    directory=tmp_path.resolve(); directory.chmod(0o700)
    first=monitor.save_sample(directory,raw_snapshot(),None)
    missing=raw_snapshot(now=101); missing['checks']={}
    second=monitor.save_sample(directory,missing,first)
    assert second['checks']['process.api']==monitor.finding('unknown','not_observed')
    recovered=monitor.save_sample(directory,raw_snapshot('pass','matched',102),second)
    assert recovered['events'][-1]['kind']=='recovered'


def test_backup_directory_scan_stops_at_the_257th_entry(saved,monkeypatch):
    root,_=saved; data=read_private(root/'INSTANCE.json'); count=[]
    original=Path.iterdir
    def entries(path):
        if path!=root/'backups': return original(path)
        def bounded():
            for index in range(258):
                count.append(index)
                if index==257: pytest.fail('Traversal continued beyond the scan bound')
                yield path/('entry-'+str(index))
        return bounded()
    monkeypatch.setattr(Path,'iterdir',entries)
    checks=monitor.backup_checks(root,data,POLICY,1800000000)
    assert len(count)==257 and checks['backup']['status']=='fail'


@pytest.mark.parametrize('watch,max_samples',[(True,1),(True,0),(False,2),(False,0)])
def test_repeated_observation_rejects_full_backup_hash_before_opening_instance(tmp_path,watch,max_samples):
    root=tmp_path/'not-created'
    with pytest.raises(InstanceError,match='single check'):
        monitor.run(root,{**POLICY,'verify_backup':True},watch=watch,max_samples=max_samples)
    assert not root.exists()


def test_watch_cli_rejects_full_backup_hash_even_for_one_sample(tmp_path,monkeypatch,capsys):
    root=tmp_path/'not-created'
    monkeypatch.setattr(sys,'argv',['instance_monitor.py','watch','--root',str(root),
                                  '--verify-backup','--max-samples','1'])
    with pytest.raises(SystemExit) as exc: monitor.main()
    assert exc.value.code==2
    assert 'never watch' in capsys.readouterr().err and not root.exists()
