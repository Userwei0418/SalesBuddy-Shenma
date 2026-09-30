"""Exercise upgrade/rollback service orchestration without SSH or systemd writes."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC=importlib.util.spec_from_file_location('shenma_upgrade_sales',Path(__file__).with_name('upgrade-sales.py'))
upgrade=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


@pytest.mark.parametrize('load,active,stop_extra,start_extra',[
    ('not-found',False,False,False),('loaded',False,True,False),('loaded',True,True,True)])
def test_optional_service_inventory_preserves_stopped_worker(monkeypatch,load,active,stop_extra,start_extra):
    monkeypatch.setattr(upgrade.subprocess,'check_output',lambda *a,**k:load+'\n')
    monkeypatch.setattr(upgrade.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0 if active else 3))
    stop,start=upgrade.existing_services()
    assert (upgrade.FEISHU_SERVICE in stop)==stop_extra
    assert (upgrade.FEISHU_SERVICE in start)==start_extra
    assert stop[:2]==start[:2]==upgrade.CORE_SERVICES


def test_invalid_optional_unit_fails_before_stopping_services(monkeypatch):
    monkeypatch.setattr(upgrade.subprocess,'check_output',lambda *a,**k:'error\n')
    with pytest.raises(RuntimeError,match='loaded or absent'):
        upgrade.existing_services()


@pytest.mark.parametrize('optional',[False,True])
@pytest.mark.parametrize('fail_health',[False,True])
@pytest.mark.parametrize('before_schema,after_schema',[('V156','V157'),('V158','V159')])
def test_upgrade_and_rollback_use_same_service_inventory(tmp_path,monkeypatch,optional,fail_health,before_schema,after_schema):
    old_revision,new_revision='a'*40,'b'*40
    root=tmp_path/'sales';old=root/'releases'/old_revision;old.mkdir(parents=True)
    (old/'REVISION').write_text(old_revision+'\n')
    (old/'database').mkdir();(old/'database'/'old.sql').write_text('-- historical\n')
    (root/'current').symlink_to(old)
    archive=tmp_path/'source.tgz';archive.write_bytes(b'synthetic source archive')
    wheels=tmp_path/'wheels.tgz';wheels.write_bytes(b'synthetic wheels archive')
    backups=tmp_path/'backups'
    real_path=Path
    monkeypatch.setattr(upgrade,'Path',lambda p:root if str(p)=='/opt/shenma-sales' else
        backups if str(p)=='/var/backups/shenma-sales' else
        tmp_path/'absent-model-key' if str(p)=='/var/lib/shenma-model-key' else real_path(p))
    monkeypatch.setattr(upgrade.os,'geteuid',lambda:0)
    monkeypatch.setattr(upgrade.os,'umask',lambda value:0o077)
    monkeypatch.setattr(upgrade.socket,'gethostname',lambda:'salesbuddy')
    monkeypatch.setattr(upgrade.subprocess,'check_output',lambda *a,**k:'172.22.9.234\n')
    units=(*upgrade.CORE_SERVICES,upgrade.FEISHU_SERVICE) if optional else upgrade.CORE_SERVICES
    monkeypatch.setattr(upgrade,'existing_services',lambda:(units,units))
    def unpack(_archive,destination,prefix=''):
        if not prefix:return
        (destination/'database').mkdir();(destination/'database'/'old.sql').write_text('-- historical\n')
        (destination/'backend').mkdir();(destination/'frontend').mkdir()
        (destination/'REVISION').write_text(new_revision+'\n')
        (destination/'frontend'/'project.config.json').write_text(json.dumps({'appid':'wx2824bdeb58528fd8'}))
    monkeypatch.setattr(upgrade,'unpack',unpack)
    calls=[];schema=before_schema;migrations=0
    def run(args,**kwargs):
        nonlocal schema,migrations
        calls.append(list(map(str,args)))
        if str(args[-1]).endswith('database/scripts/migrate.py'):
            migrations+=1;schema=after_schema
            kwargs['stdout'].write(json.dumps([{'status':'applied' if migrations==1 else 'unchanged'}]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(upgrade,'run',run)
    def sql(database,query):
        if 'max(version)' in query:return schema if database=='shenma_sales' else before_schema
        if 'FROM pg_tables' in query:return 'crm.customer'
        if 'count(*)' in query:return '1'
        return 'same-checksum'
    monkeypatch.setattr(upgrade,'sql',sql)
    health=[]
    def healthy(revision,services):
        health.append((revision,services))
        if fail_health and revision==new_revision:raise RuntimeError('synthetic health failure')
        return {'revision':revision}
    monkeypatch.setattr(upgrade,'healthy',healthy)
    args=['upgrade-sales.py','--archive',str(archive),'--archive-sha256',hashlib.sha256(archive.read_bytes()).hexdigest(),
        '--wheels',str(wheels),'--wheels-sha256',hashlib.sha256(wheels.read_bytes()).hexdigest(),
        '--revision',new_revision,'--expected-current',old_revision,'--expected-schema',before_schema,'--target-schema',after_schema]
    monkeypatch.setattr('sys.argv',args)
    if fail_health:
        with pytest.raises(RuntimeError,match='synthetic health failure'):upgrade.main()
    else:upgrade.main()
    service_calls=[c for c in calls if c[0]=='systemctl']
    assert service_calls==[['systemctl',action,*units] for action in (['stop','start','stop','start'] if fail_health else ['stop','start'])]
    assert health==[(new_revision,units)]+([(old_revision,units)] if fail_health else [])
    assert (root/'current').resolve()==(old if fail_health else root/'releases'/new_revision)


def test_health_checks_optional_worker_together_with_api(monkeypatch):
    class Response(io.StringIO):
        status=200
    monkeypatch.setattr(upgrade.urllib.request,'urlopen',lambda *a,**k:Response('{"revision":"revision"}'))
    calls=[]
    monkeypatch.setattr(upgrade,'run',lambda args,**kwargs:calls.append(args))
    units=(*upgrade.CORE_SERVICES,upgrade.FEISHU_SERVICE)
    assert upgrade.healthy('revision',units)=={'revision':'revision'}
    assert calls==[['systemctl','is-active','--quiet',unit] for unit in units]


def test_optional_worker_failure_prevents_success_even_when_api_is_active(monkeypatch):
    class Response(io.StringIO):
        status=200
    monkeypatch.setattr(upgrade.urllib.request,'urlopen',lambda *a,**k:Response('{"revision":"revision"}'))
    monkeypatch.setattr(upgrade.time,'sleep',lambda _:None)
    def run(args,**kwargs):
        if args[-1]==upgrade.FEISHU_SERVICE:raise RuntimeError('synthetic inactive worker')
    monkeypatch.setattr(upgrade,'run',run)
    with pytest.raises(RuntimeError,match='health verification failed'):
        upgrade.healthy('revision',(*upgrade.CORE_SERVICES,upgrade.FEISHU_SERVICE))
