"""Read-only instance diagnostics and private, bounded local incident history.

check samples once; watch samples periodically without installing a scheduler.
No database credentials, business SQL, service changes or outbound integrations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

if __name__ == '__main__': sys.dont_write_bytecode = True

from instance_files import (InstanceError, atomic_json, instance, read_private, root_path,
                            runtime_path, source_check)
from instance_process import ProcessIdentityChanged, state
from monitor_http import health_json, listener_owned
from release_bundle import BundleError

FORMAT = 'salesbuddy-instance-monitor-v1'
LEVELS = {'pass':0, 'unknown':1, 'degraded':2, 'fail':3}
ERRORS = (InstanceError, BundleError, OSError, ValueError, KeyError, TypeError,
          subprocess.SubprocessError)
EVENT_LIMIT = 200
CHECK_KEYS = ('process.api','process.worker','api.live','api.version','database','authentication',
              'model','backup','backup.integrity','backup.restore','backup.incomplete')


def finding(status, code, **values):
    return {'status':status, 'code':code, **values}


def process(root, data, service):
    try:
        record = state(root, service, data['instance_id'])
        if record is None: return finding('fail', 'stopped'), None
        expected = (str(runtime_path(root, data)/'bin/python')+' '+
            str(root/'release/tools/installation/instance_service.py')+
            ' --root '+str(root)+' --service '+service+' --instance-id '+data['instance_id'])
        if record['command'] != expected: return finding('fail', 'unexpected_command'), None
        return finding('pass', 'identity_matched', pid=record['pid']), record
    except ProcessIdentityChanged: return finding('fail', 'identity_changed'), None
    except ERRORS: return finding('unknown', 'identity_unavailable'), None


def api_checks(root, data, record, timeout):
    result = {key:finding('unknown', 'api_identity_unavailable') for key in
              ('api.live', 'api.version', 'database', 'authentication', 'model')}
    if not record: return result
    try:
        if not listener_owned(data['api_port'], record['pid']):
            result['api.live'] = finding('fail', 'listener_identity_mismatch')
            return result
        replies = {name:health_json(data['api_port'], name, timeout=timeout)
                   for name in ('live', 'version', 'ready')}
        # Recheck both process incarnation and listener after reads: a process
        # exiting/reusing the port mid-sample never earns a healthy result.
        _, again = process(root, data, 'api')
        if again != record or not listener_owned(data['api_port'], record['pid']):
            result['api.live'] = finding('fail', 'identity_changed_during_sample')
            return result
    except ERRORS:
        result['api.live'] = finding('fail', 'health_unavailable_or_invalid')
        return result
    code, live = replies['live']
    result['api.live'] = finding('pass', 'alive') if code == 200 and live.get('status') == 'ok' else finding('fail', 'invalid_live')
    code, version = replies['version']
    matched = code == 200 and version.get('revision') == data['revision'] and version.get('expected_schema') == data['expected_schema']
    result['api.version'] = finding('pass', 'matched') if matched else finding('fail', 'version_mismatch')
    if not matched: return result
    code, ready = replies['ready']; checks = ready.get('checks')
    if not isinstance(checks, dict) or any(type(checks.get(k)) is not bool for k in
            ('database','auth_configured','model_gateway_configured')):
        result['database'] = finding('unknown', 'invalid_readiness')
        return result
    consistent = (code == 200 and all(checks[k] for k in ('database','auth_configured','model_gateway_configured'))
                  and ready.get('status') == 'ok') or (code == 503 and not all(checks[k] for k in
                    ('database','auth_configured','model_gateway_configured')) and ready.get('status') == 'degraded')
    if not consistent:
        result['database'] = finding('unknown', 'inconsistent_readiness')
        return result
    result['database'] = finding('pass', 'ready') if checks['database'] else finding('fail', 'not_ready')
    result['authentication'] = finding('pass', 'configured') if checks['auth_configured'] else finding('fail', 'not_configured')
    result['model'] = finding('pass', 'configured') if checks['model_gateway_configured'] else finding('degraded', 'not_configured')
    return result


def disk_check(root, policy):
    try:
        total, used, free = shutil.disk_usage(root)
        if total <= 0 or used < 0 or free < 0 or used > total or free > total:
            raise ValueError('Invalid filesystem statistics')
        percent = 100*used/total
        low = free < policy['disk_min_free_bytes'] or percent >= policy['disk_max_used_percent']
        return finding('fail' if low else 'pass', 'low_space' if low else 'available',
                       free_bytes=free, total_bytes=total, used_percent=round(percent, 2))
    except ERRORS: return finding('unknown', 'filesystem_unavailable')


def backup_checks(root, data, policy, now):
    # No maintenance connection. Validation reads only local files and never
    # invokes pg_dump/pg_restore or starts a restore probe.
    from instance_backup import BACKUP_NAME, MAX_MANIFEST_BYTES, digest, validate_backup
    result = {'backup':finding('degraded', 'missing'), 'backup.incomplete':finding('pass','none'),
              'backup.integrity':finding('unknown', 'no_complete_backup'),
              'backup.restore':finding('unknown', 'no_matching_evidence')}
    parent = root/'backups'
    if not parent.exists() and not parent.is_symlink(): return result
    try:
        root_path(parent)
        names = list(islice(parent.iterdir(), 257))
        if len(names) > 256: raise InstanceError('Backup scan limit exceeded')
        names = sorted((p for p in names if BACKUP_NAME.fullmatch(p.name)), reverse=True)
        complete, incomplete = [], 0
        for directory in names:
            root_path(directory)
            path = directory/'BACKUP.json'
            if not path.exists() and not path.is_symlink(): incomplete += 1
            else: complete.append(directory)
        if incomplete: result['backup.incomplete'] = finding('degraded', 'incomplete_present', count=incomplete)
        else: result['backup.incomplete'] = finding('pass', 'none')
        if not complete: return result
        directory = complete[0]
        manifest = read_private(directory/'BACKUP.json', max_bytes=MAX_MANIFEST_BYTES)
        if (manifest.get('format') != 'salesbuddy-instance-backup-v1' or manifest.get('instance_id') != data['instance_id']
                or manifest.get('source_root') != str(root) or not isinstance(manifest.get('files'), dict)
                or not re.fullmatch('[0-9a-f]{40}', manifest.get('revision', ''))):
            raise InstanceError('Backup identity invalid')
        created = datetime.fromisoformat(manifest['created_at'])
        if created.tzinfo is None: raise ValueError('Backup timestamp has no timezone')
        age = now-created.timestamp()
        sha = digest(directory/'BACKUP.json')
        if age < -5: result['backup'] = finding('unknown', 'future_timestamp', name=directory.name)
        else: result['backup'] = finding('degraded' if age > policy['backup_max_age_seconds'] else 'pass',
            'expired' if age > policy['backup_max_age_seconds'] else 'recent', name=directory.name,
            age_seconds=max(0, round(age)), manifest_sha256=sha)
        result['backup.integrity'] = finding('unknown', 'not_verified')
        if policy['verify_backup']:
            try:
                checked, _ = validate_backup(root, directory)
                if checked != manifest or digest(directory/'BACKUP.json') != sha:
                    raise InstanceError('Backup changed while validating')
                result['backup.integrity'] = finding('pass', 'verified', manifest_sha256=sha)
            except ERRORS: result['backup.integrity'] = finding('fail', 'invalid_or_changed')
        receipt = root/'run/last-restore-check.json'
        if receipt.exists() or receipt.is_symlink():
            try:
                evidence = read_private(receipt)
                matched = (evidence.get('status') == 'passed' and evidence.get('backup') == str(directory)
                    and evidence.get('backup_manifest_sha256') == sha and evidence.get('revision') == manifest['revision']
                    and evidence.get('probe_database_removed') is True)
                result['backup.restore'] = finding('pass', 'matching_same_cluster_probe', manifest_sha256=sha) if matched else finding('unknown', 'receipt_mismatch')
            except ERRORS: result['backup.restore'] = finding('unknown', 'receipt_invalid')
        if result['backup.integrity']['status'] == 'fail':
            result['backup.restore'] = finding('unknown', 'current_backup_invalid')
        return result
    except ERRORS:
        result['backup'] = finding('fail', 'metadata_or_path_invalid')
        result['backup.integrity'] = finding('unknown', 'cannot_verify')
        return result


def sample(root, policy, *, now=None):
    now = time.time() if now is None else now
    checks = {key:finding('unknown','not_observed') for key in CHECK_KEYS}; data = None
    if policy['feishu_required']: checks['process.feishu'] = finding('unknown','not_observed')
    try:
        data = instance(root)
        pending = any((root/p).exists() or (root/p).is_symlink() for p in
            ('run/upgrade.json','run/recovery.json','release/RECOVERY_PENDING'))
        if pending: checks['instance'] = finding('degraded', 'maintenance_pending')
        else:
            checked, _ = source_check(root)
            if checked != data: raise InstanceError('Instance changed while sampling')
            initialized = read_private(root/'run/initialized.json')
            checks['instance'] = finding('pass','verified') if initialized == {
                'instance_id':data['instance_id'],'revision':data['revision']} else finding('fail','initialization_mismatch')
    except ERRORS: checks['instance'] = finding('fail', 'package_or_identity_invalid')
    checks['disk'] = disk_check(root, policy)
    if data and checks['instance']['status'] == 'pass':
        records = {}
        for service in ('api','worker','feishu') if policy['feishu_required'] else ('api','worker'):
            checks['process.'+service], records[service] = process(root, data, service)
        checks.update(api_checks(root, data, records['api'], policy['http_timeout_seconds']))
        checks.update(backup_checks(root, data, policy, now))
    else:
        checks['database'] = finding('unknown', 'instance_not_verified')
    if data and data['database']['host'] not in {'127.0.0.1','localhost','::1'}:
        checks['remote_database_disk'] = finding('unknown', 'not_checked')
    return {'format':FORMAT, 'instance_id':data['instance_id'] if data else None,
        'revision':data['revision'] if data else None, 'sampled_at':now, 'checks':checks,
        'policy':policy, 'worker_progress_verified':False, 'database_ledger_checked':False,
        'external_integrations_verified':False, 'offsite_recovery_verified':False}


@contextmanager
def observer(root):
    root = root_path(root); root_path(root/'run')
    directory = root/'run/monitor'
    directory.mkdir(mode=0o700, exist_ok=True); root_path(directory)
    descriptor = os.open(directory/'observer.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise InstanceError('Observer lock is not private')
        try: fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise InstanceError('Another observer is running') from None
        yield directory
    finally: os.close(descriptor)


def load_previous(directory):
    path = directory/'state.json'
    if not path.exists() and not path.is_symlink(): return None
    value = read_private(path, max_bytes=1048576)
    if (value.get('format') != FORMAT or not isinstance(value.get('checks'), dict)
            or not isinstance(value.get('events'), list) or len(value['events']) > EVENT_LIMIT
            or type(value.get('sequence')) is not int or value['sequence'] < 0
            or not isinstance(value.get('sampled_at'), (int,float)) or not math.isfinite(value['sampled_at'])):
        raise InstanceError('Monitor state invalid; retained for diagnosis')
    for key, item in value['checks'].items():
        if (not isinstance(key,str) or not isinstance(item,dict) or item.get('status') not in LEVELS
                or not isinstance(item.get('code'),str)):
            raise InstanceError('Monitor state invalid; retained for diagnosis')
    previous_sequence = 0
    for event in value['events']:
        if (not isinstance(event,dict) or set(event) != {'sequence','sampled_at','check','from','to','code','kind'}
                or type(event['sequence']) is not int or not previous_sequence <= event['sequence'] <= value['sequence']
                or event['to'] not in LEVELS or event['from'] not in {None,*LEVELS}
                or not isinstance(event['check'],str) or not isinstance(event['code'],str)
                or event['kind'] not in {'first_failure','changed','recovered'}
                or not isinstance(event['sampled_at'],(int,float)) or not math.isfinite(event['sampled_at'])):
            raise InstanceError('Monitor events invalid; retained for diagnosis')
        previous_sequence = event['sequence']
    return value


def save_sample(directory, current, previous):
    if previous and previous.get('instance_id') and current['instance_id'] and previous['instance_id'] != current['instance_id']:
        raise InstanceError('Monitor state belongs to another instance')
    if previous and current['instance_id'] is None: current['instance_id'] = previous.get('instance_id')
    checks = current['checks']
    # Never discard an incident just because maintenance or failed identity
    # verification prevented observing that subsystem this round. This also
    # retains explicitly configured checks when a later invocation omits them.
    if previous:
        for key in previous['checks']:
            checks.setdefault(key, finding('unknown','not_observed'))
    if previous and current['sampled_at'] < previous['sampled_at']-5:
        checks['clock'] = finding('unknown', 'wall_clock_moved_backwards')
    else: checks['clock'] = finding('pass','consistent')
    sequence = (previous['sequence'] if previous else 0)+1
    events = list(previous['events']) if previous else []
    for key, item in sorted(checks.items()):
        old = previous['checks'].get(key) if previous else None
        changed = old and (old['status'] != item['status'] or (item['status'] != 'pass' and old.get('code') != item['code']))
        if (old is None and item['status'] != 'pass') or changed:
            events.append({'sequence':sequence,'sampled_at':current['sampled_at'],'check':key,
                'from':old['status'] if old else None,'to':item['status'],'code':item['code'],
                'kind':'recovered' if item['status'] == 'pass' else 'changed' if old else 'first_failure'})
    current.update(sequence=sequence,events=events[-EVENT_LIMIT:],
                   status=max((v['status'] for v in checks.values()), key=LEVELS.__getitem__))
    # Snapshot and event ring share one atomic file: a crash cannot commit one
    # without the other and then duplicate or lose the corresponding transition.
    atomic_json(directory/'state.json',current)
    return current


def run(root, policy, *, interval=60, max_samples=1, watch=False):
    if not math.isfinite(interval) or interval < 1 or max_samples < 0:
        raise InstanceError('Invalid watch bounds')
    if policy['verify_backup'] and (watch or max_samples != 1):
        raise InstanceError('Full backup verification is supported only by a single check')
    with observer(root) as directory:
        previous = load_previous(directory)
        count = 0
        while True:
            started = time.monotonic()
            current = save_sample(directory, sample(root,policy), previous)
            print(json.dumps({'status':current['status'],'sequence':current['sequence'],
                              'snapshot':str(directory/'state.json')}, ensure_ascii=False),flush=True)
            previous = current; count += 1
            if max_samples and count >= max_samples: return current
            time.sleep(max(0, interval-(time.monotonic()-started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check','watch'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--feishu-required', action='store_true')
    parser.add_argument('--verify-backup', action='store_true')
    parser.add_argument('--disk-min-free-bytes', type=int, default=1024**3)
    parser.add_argument('--disk-max-used-percent', type=float, default=95)
    parser.add_argument('--backup-max-age-seconds', type=int, default=86400)
    parser.add_argument('--http-timeout-seconds', type=float, default=3)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--max-samples', type=int, default=0)
    args = parser.parse_args()
    if args.command == 'watch' and args.verify_backup:
        parser.error('--verify-backup is supported only with check, never watch')
    policy = {key:getattr(args,key) for key in ('feishu_required','verify_backup','disk_min_free_bytes',
        'disk_max_used_percent','backup_max_age_seconds','http_timeout_seconds')}
    if (policy['disk_min_free_bytes'] < 0 or not 0 < policy['disk_max_used_percent'] <= 100
            or policy['backup_max_age_seconds'] < 1 or not 0 < policy['http_timeout_seconds'] <= 30):
        parser.error('Invalid diagnostic threshold')
    current = run(root_path(args.root), policy, interval=args.interval,
                  max_samples=1 if args.command == 'check' else args.max_samples, watch=args.command == 'watch')
    return {'pass':0,'degraded':1,'fail':2,'unknown':3}[current['status']]


if __name__ == '__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
    except ERRORS: raise SystemExit('Monitor failed; previous private state retained; no services were changed') from None
