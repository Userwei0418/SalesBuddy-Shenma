"""Real CLI/watch/lock verification on a synthetic, unconnected instance.

No database connection or runtime process is created. The optional helper
verify_running() is intended for verify_product_instance's existing disposable
live-services fixture, after the implementation is integrated and committed.
"""
from argparse import ArgumentParser, Namespace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

DRAFT = Path(__file__).resolve().parents[3]
SOURCE = Path(os.environ.get('SALESBUDDY_SOURCE_ROOT', DRAFT))
sys.path[:0] = [str(DRAFT/'tools/installation'),str(SOURCE/'tools/installation')]
from instance import prepare as instance_prepare
from instance_files import atomic_json, read_private
from release_bundle import committed_files, prepare, read_verified, write_archive


def command(root,*extra,timeout=30):
    return subprocess.run([sys.executable,str(DRAFT/'tools/installation/instance_monitor.py'),
        *extra,'--root',str(root),'--disk-min-free-bytes','0','--disk-max-used-percent','100'],
        capture_output=True,text=True,timeout=timeout)


def verify_running(root):
    """Hook for the existing isolated product installation system test."""
    result=command(root,'check','--feishu-required')
    assert result.returncode==1, 'Live fixture should be degraded only by unconfigured model/missing backups'
    snapshot=read_private(root/'run/monitor/state.json')
    for name in ('instance','process.api','process.worker','process.feishu','api.live','api.version','database','authentication'):
        assert snapshot['checks'][name]['status']=='pass', name
    assert snapshot['checks']['model']['code']=='not_configured'
    return {'live_instance_checked':True,'database_ready':True,'model_unconfigured_separate':True}


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--sha256')
    args=parser.parse_args()
    if bool(args.archive) != bool(args.sha256): parser.error('--archive and --sha256 must be provided together')
    fixture=Path(tempfile.mkdtemp(prefix='salesbuddy-monitor-test-')).resolve()
    root=fixture/'instance'
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1',0)); port=port_probe.getsockname()[1]
    if args.archive:
        _,manifest=read_verified(args.archive,args.sha256)
        if manifest['profile']!='local': parser.error('The offline fixture needs a local candidate')
        revision=manifest['revision']; port=urlsplit(manifest['api_base_url']).port or 80
        archive,sha=args.archive,args.sha256
    else:
        revision,files,modes=committed_files(SOURCE)
        files,modes,_=prepare(files,modes,revision=revision,api_url=f'http://127.0.0.1:{port}/api/v1',
            appid='wx0123456789abcdef',profile='local',product_name='monitor-synthetic-check')
        archive=fixture/'candidate.zip'; sha=write_archive(files,modes,archive)
    instance_prepare(Namespace(root=root,archive=archive,sha256=sha,database='synthetic_not_created',
        db_host='127.0.0.1',db_port=1,db_user='synthetic_not_created',db_sslmode='disable',
        api_port=port,operator='MONITORTEST',operator_name='Synthetic monitor test'))
    data=read_private(root/'INSTANCE.json')
    # Synthetic receipt enables diagnosis of stopped services without claiming a
    # real database initialization. There is no connection to any PostgreSQL.
    atomic_json(root/'run/initialized.json',{'instance_id':data['instance_id'],'revision':data['revision']})
    first=command(root,'check'); assert first.returncode==2
    state=read_private(root/'run/monitor/state.json')
    assert state['checks']['process.api']['code']=='stopped'
    assert state['checks']['database']['status']=='unknown'
    count=len(state['events'])
    watched=command(root,'watch','--interval','1','--max-samples','3')
    assert watched.returncode==2 and len(watched.stdout.strip().splitlines())==3
    state=read_private(root/'run/monitor/state.json')
    assert state['sequence']==4 and len(state['events'])==count
    forbidden=command(root,'watch','--max-samples','1','--verify-backup')
    assert forbidden.returncode==2 and 'never watch' in forbidden.stderr
    assert read_private(root/'run/monitor/state.json')==state
    child=subprocess.Popen([sys.executable,str(DRAFT/'tools/installation/instance_monitor.py'),
        'watch','--root',str(root),'--interval','2','--max-samples','2'],stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+15
        while read_private(root/'run/monitor/state.json')['sequence']<5:
            assert child.poll() is None and time.monotonic()<deadline
            time.sleep(.05)
        concurrent=command(root,'check'); assert concurrent.returncode!=0
        assert 'Monitor failed' in concurrent.stderr
        assert child.wait(timeout=15)==2
    finally:
        if child.poll() is None: child.terminate(); child.wait(timeout=5)
    assert not any((root/'run'/f'{name}.json').exists() for name in ('api','worker','feishu'))
    assert (root/'run/monitor/state.json').stat().st_mode&0o077==0
    print(json.dumps({'status':'passed','fixture':str(fixture),'source_revision':revision,
        'checks':['stopped_services_snapshot','unknown_database','bounded_watch','incident_deduplication',
                  'cross_process_observer_lock','private_state','watch_full_hash_refused'],
        'database_created':False,'services_started':False,'production_contacted':False,
        'live_services_hook_executed':False},indent=2))


if __name__=='__main__': main()
