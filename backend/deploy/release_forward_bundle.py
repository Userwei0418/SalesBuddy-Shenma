"""Explicit managed-host forward migration; default is files-only.

The original same-ledger register/switch/recover implementation is unchanged.
After starting the database phase, any failure leaves services stopped. There
is no retry, historical checksum adoption, database restore or old-code restart.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
from datetime import datetime, timezone

import release_bundle_control as original
from release_upgrade import verify_migration_plan

Refused = original.Refused
ROOT = Path('/www/wwwroot/salegent')
WEB = Path('/var/www/salesbuddy-web')


def require(value, code):
    if not value:
        raise Refused(code)


def safe_error(exc):
    value = {'type': type(exc).__name__}
    if isinstance(exc, Refused) or (type(exc).__module__ in ('_forward_production_acl', 'release_upgrade')
            and type(exc).__name__ in ('Refused', 'ReleaseError')):
        value['code'] = str(exc)  # these fixed validators contain no SQL/credentials
    return value


def read_ref(ref):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'Exact file reference required')
    path = Path(ref['path'])
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'Regular absolute file required')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Input file changed')
    return raw


def document(ref):
    return json.loads(read_ref(ref))


def file_ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def new_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return file_ref(path)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pointer_stamp(root):
    path = root / 'current'
    info = path.lstat()
    return {'release': original.pointer(root), 'link': os.readlink(path),
            'device': info.st_dev, 'inode': info.st_ino, 'mtime_ns': info.st_mtime_ns}


def check_pointers(host, expected):
    require({k: pointer_stamp(root) for k, root in [('backend', host.root), ('web', host.web)]}
            == expected, 'Current pointer identity changed')


def verify_release_ledger(p, migrations, observed, *, final=False):
    """Keep the historical-migration exception bound at every ledger check."""
    contract_id = None
    if p.get('release_scope') == ['review_okr_1.2.1']:
        from review_release_contract import validate, validate_migrations
        validate(p, migrations, digest=original.digest, require=require)
        pending = [row['key'] for row in migrations if row['key'] not in observed['checksums']]
        validate_migrations(migrations, observed['checksums'], observed['registered'],
            p['target_schema'] if final else p['source_schema'], p['target_schema'], pending,
            digest=original.digest, require=require)
    elif p.get('release_scope') == ['enterprise_identity_1.2.1']:
        from identity_release_contract import validate, validate_migrations
        validate(p, migrations, digest=original.digest, require=require)
        pending = [row['key'] for row in migrations if row['key'] not in observed['checksums']]
        validate_migrations(migrations, observed['checksums'], observed['registered'],
            p['target_schema'] if final else p['source_schema'], p['target_schema'], pending,
            digest=original.digest, require=require)
    elif p['product_version'] == '1.2.1':
        from release121_contract import CONTRACT_ID, validate
        validate(p, migrations, digest=original.digest, require=require)
        contract_id = CONTRACT_ID
    return verify_migration_plan(migrations, observed['checksums'], observed['registered'],
        p['target_schema'] if final else p['source_schema'], p['target_schema'],
        release_contract=contract_id)


def inputs(plan_ref):
    p = document(plan_ref)
    for key in ('bundle_id', 'backend', 'web', 'expected_backend', 'expected_web'):
        original.safe_id(p[key])
    report_hotfix = (p['source_schema'], p['target_schema']) == ('V232', 'V233')
    finance234 = (p['source_schema'], p['target_schema']) == ('V233', 'V234')
    report_only = (p['source_schema'], p['target_schema']) == ('V233', 'V238')
    release121 = p['product_version'] == '1.2.1' and (p['source_schema'], p['target_schema']) == ('V238', 'V238')
    identity239 = p['product_version'] == '1.2.1' and (p['source_schema'], p['target_schema']) == ('V238', 'V239')
    review_okr = p['product_version'] == '1.2.1' and p.get('release_scope') == ['review_okr_1.2.1']
    require(release121 or identity239 or review_okr or (p['product_version'] == '1.2.0' and (
        (p['source_schema'], p['target_schema']) == ('V202', 'V232') or report_hotfix or finance234 or report_only)),
        'Exact release target required')
    require(p['expected_database']['name'] == 'sales_saas', 'Production database required')
    require(p['expected_backend'] != p['backend'], 'New backend release identity required')
    if report_hotfix:
        require(p['expected_web'] == p['web'] and p['pending'] == ['V233']
                and p.get('agent_stage') is None and p.get('prepared_configuration_delta') is None,
                'V233 must reuse exact Web and Agent configuration with only V233 pending')
    else:
        require(p['expected_web'] != p['web'], 'New Web release identity required')
    migration_plan = document(p['migration_plan'])
    if review_okr:
        from review_release_contract import validate
        validate(p, migration_plan, digest=original.digest, require=require)
    if report_only:
        from report_release_contract import validate
        validate(p, migration_plan, digest=original.digest, require=require)
    if release121:
        from release121_contract import validate
        validate(p, migration_plan, digest=original.digest, require=require)
    if identity239:
        from identity_release_contract import validate
        validate(p, migration_plan, digest=original.digest, require=require)
    if finance234:
        # Exact reviewed successor, not a generic arbitrary-schema upgrade.
        require(p['pending'] == ['V234'] and p.get('agent_stage') is None
                and p.get('prepared_configuration_delta') is None,
                'V234 changes only the paired finance source/schema; Agent configuration must stay fixed')
        before = p['expected_ledger']['checksums']
        after = {row['key']: row['sha256'] for row in migration_plan}
        require(len(before) == 197 and original.digest(before) ==
                '4838fdac5ab18e821bd51e794b63b179f9ef467e3ea2d47e961426603bf417a4'
                and original.digest(sorted(p['expected_ledger']['registered'])) ==
                '51c8d2b81183bc70d6b3a28a172d44f036dc2dc5005d66fd7d0ac118bf596b6e'
                and len(migration_plan) == len(after) == 198 and original.digest(after) ==
                '43b782d87d8b4d181ccc1861c5bcfa504a54ad575a42c17a8186926b7a67e2ca'
                and next((row for row in migration_plan if row['key'] == 'V234'), None) == {
                    'key': 'V234', 'file': 'migrations/V234__opportunity_product_line_intents.sql',
                    'sha256': '576f5d710e08090b150fbcbc0b1c1e30d96561448bae19a5e1a2c29dd2b53490'},
                'Exact accepted197 prefix and only fixed V234 required')
    evidence = document(p['validation'])
    read_ref(p['acl_source']); document(p['acl_plan'])
    if report_only:
        acl_plan = document(p['acl_plan'])
        from report_release_contract import validate_acl
        validate_acl(acl_plan, document(acl_plan['policy_ref']), digest=original.digest, require=require)
        contract_path = ROOT/'releases'/p['backend']/'backend/deploy/report_release_contract.py'
        require(contract_path.is_file() and not contract_path.is_symlink()
                and acl_plan.get('source_files', {}).get(str(contract_path))
                    == hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                'Report release contract must be fixed by the reviewed ACL source closure')
    if release121:
        acl_plan = document(p['acl_plan'])
        from release121_contract import validate_acl
        validate_acl(acl_plan, document(acl_plan['policy_ref']), digest=original.digest, require=require)
        for name in ('release121_contract.py', 'release_upgrade.py', 'release_forward_bundle.py'):
            source = ROOT/'releases'/p['backend']/'backend/deploy'/name
            require(source.is_file() and not source.is_symlink()
                    and acl_plan.get('source_files', {}).get(str(source))
                        == hashlib.sha256(source.read_bytes()).hexdigest(),
                    '1.2.1 release gates must be fixed by the reviewed ACL source closure')
    if identity239:
        acl_plan = document(p['acl_plan'])
        from identity_release_contract import validate_acl
        validate_acl(acl_plan, document(acl_plan['policy_ref']), digest=original.digest, require=require)
        for name in ('identity_release_contract.py', 'release121_contract.py', 'release_upgrade.py',
                     'release_forward_bundle.py', 'release120_production_acl.py'):
            source = ROOT/'releases'/p['backend']/'backend/deploy'/name
            require(source.is_file() and not source.is_symlink()
                    and acl_plan.get('source_files', {}).get(str(source))
                        == hashlib.sha256(source.read_bytes()).hexdigest(),
                    'Identity release gates must be fixed by the reviewed ACL source closure')
    if review_okr:
        acl_plan = document(p['acl_plan'])
        from review_release_contract import GATE_FILES, validate_acl
        validate_acl(acl_plan, document(acl_plan['policy_ref']), digest=original.digest, require=require)
        require(max(acl_plan['expected_registered_versions'], key=lambda value: int(value[1:]))
                == p['target_schema'], 'Review forward and ACL target differ')
        for name in GATE_FILES:
            source = ROOT/'releases'/p['backend']/'backend/deploy'/name
            require(source.is_file() and not source.is_symlink()
                    and acl_plan.get('source_files', {}).get(str(source))
                        == hashlib.sha256(source.read_bytes()).hexdigest(),
                    'Review release gates must be fixed by the reviewed ACL source closure')
    delta = p.get('prepared_configuration_delta')
    if delta is not None:
        require(set(delta) == {'old_configuration','prepared_configuration','evidence'}, 'Exact prepared configuration delta required')
        document(delta['evidence'])  # root-fixed nonsecret receipt for the two additive EnvironmentFiles/drop-ins
        old_cfg, new_cfg = delta['old_configuration'], delta['prepared_configuration']
        require(set(old_cfg) == set(new_cfg) == {'service_metadata_sha256','agent_metadata'}
                and old_cfg['agent_metadata'] == new_cfg['agent_metadata'],
                'Prepared environment must preserve existing Agent metadata')
    if p.get('agent_stage') is not None:
        stage = p['agent_stage']
        require(set(stage) == {'source','plan'} and Path(stage['source']['path']) ==
            ROOT/'releases'/p['backend']/'backend/deploy/release120_agent_stage.py', 'Exact candidate Agent stage required')
        read_ref(stage['source']); document(stage['plan'])
    require(Path(p['acl_source']['path']) == ROOT/'releases'/p['backend']/'backend/deploy/release120_production_acl.py',
            'ACL helper must belong to candidate deploy directory')
    require(evidence.get('status') == 'passed'
            and evidence.get('tested_source_sha256') == p['candidate']['backend']['sha256'],
            'Validation must cover final managed source')
    require(set(p['supported_mini_versions']) <= set(evidence.get('tested_mini_versions', [])),
            'Compatibility evidence missing')
    pending = verify_release_ledger(p, migration_plan, p['expected_ledger'])
    require(pending == p['pending'] and pending, 'Exact nonempty forward migration set required')
    return p, migration_plan, evidence


def source_plan(host, p):
    release = original.release_path(host.root, p['backend'])
    observed = json.loads(host.run([release/'.venv/bin/python', release/'database/scripts/migrate.py', '--plan']))
    require(observed == document(p['migration_plan']), 'Candidate migration plan changed')
    require(host.artifacts(p['backend'], p['web']) == p['candidate'], 'Candidate source or Web changed')


class ManagedForwardHost(original.Host):
    configuration_release = None

    def configuration(self):
        if self.configuration_release is None:
            return super().configuration()
        return self.configuration_for(self.configuration_release)

    def configuration_for(self, backend):
        """Original metadata checks, with both probes from one immutable release.

        This does not change the active probe release or either current pointer.
        """
        release = original.release_path(self.root, backend)
        properties = ('User', 'Group', 'WorkingDirectory', 'EnvironmentFiles', 'FragmentPath', 'DropInPaths')
        metadata = {}
        for service in original.SERVICES:
            values = dict(line.split('=', 1) for line in self.run(
                ['systemctl', 'show', service, *['-p'+p for p in properties]]).splitlines() if '=' in line)
            paths = set(original.re.findall(r'/[^\s;()]+', ' '.join(values.get(p, '') for p in
                ('EnvironmentFiles', 'FragmentPath', 'DropInPaths'))))
            stamps = {}
            for path in sorted(paths):
                info = Path(path).stat()  # metadata only; never open credential content
                stamps[path] = [info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode]
            metadata[service] = {'properties': values, 'file_metadata': stamps}
        reports = []
        for service in original.SERVICES[:2]:
            reports.append(json.loads(self.run([release/'.venv/bin/python',
                release/'backend/deploy/inherit_service_environment.py', '--service', service,
                '--unit', 'sb-bundle-config-'+original.uuid.uuid4().hex[:12], '--', release/'.venv/bin/python',
                release/'backend/deploy/verify_agent_runtime.py'])))
        require(reports[0] == reports[1], 'API and Worker Agent metadata differ')
        return {'service_metadata_sha256': original.digest(metadata), 'agent_metadata': reports[0]}


def agent_configuration(host, p, before, folder):
    stage = p['agent_stage']
    read_ref(stage['source'])
    module = load('_forward_agent_stage', Path(stage['source']['path']))
    require(callable(getattr(module, 'hook', None)), 'Agent stage hook missing')
    require(host.configuration() == before, 'Configuration changed before Agent stage')
    result = module.hook(host, document(stage['plan']), folder)
    require(result.get('configuration_before') == before
            and isinstance(result.get('configuration_after'), dict), 'Agent configuration result differs')
    host.configuration_release = p['backend']
    require(host.configuration() == result['configuration_after'], 'Agent configuration readback differs')
    read_ref(stage['source']); document(stage['plan'])
    return result


def peer_access(host, p, owned):
    account = pwd.getpwnam('postgres')
    os.chown(owned, 0, account.pw_gid)
    os.chmod(owned, 0o710)  # root owner, postgres traversal only, no global access
    acl_plan = document(p['acl_plan'])
    for bound in (p['acl_plan'], acl_plan['policy_ref']):
        read_ref(bound)
        path = Path(bound['path']); info = path.stat()
        require(info.st_uid == account.pw_uid and stat.S_IMODE(info.st_mode) == 0o600,
                'Stage ACL plan and policy must be postgres-owned 0600')
        host.run(['runuser', '-u', 'postgres', '--', 'test', '-r', path])
    ancestors = set(owned.parents) | {owned}
    for bound in (p['acl_plan'], acl_plan['policy_ref']): ancestors.update(Path(bound['path']).parents)
    for path in sorted(ancestors):
        host.run(['runuser', '-u', 'postgres', '--', 'test', '-x', path])


def peer_phase(host, plan_ref, folder):
    """One peer process, durable per-file receipts; never log subprocess output."""
    p = document(plan_ref)
    release = original.release_path(host.root, p['backend'])
    account = pwd.getpwnam('postgres')
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    request = folder/'PLAN.json'
    request_ref = new_json(request, p)
    os.chown(request, account.pw_uid, account.pw_gid)
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL') if k in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    argv = ['runuser', '-u', 'postgres', '--', str(release/'.venv/bin/python'),
        str(release/'backend/deploy/release_forward_bundle.py'), '--database-phase',
        '--plan', str(request), '--plan-sha', request_ref['sha256']]
    child = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        returncode = child.wait(timeout=7200)
    finally:
        if child.poll() is None:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait(timeout=15)
    receipt = json.loads((folder/'DATABASE.json').read_bytes())
    return {'receipt': file_ref(folder/'DATABASE.json'), 'returncode': returncode, 'result': receipt}


def forward(host, plan_ref, *, database_phase=peer_phase):
    """All managed mutations live inside one original deployment lock."""
    p, migration_plan, evidence = inputs(plan_ref)
    with host.lock():
        journal = host.state/'TRANSACTION.json'
        if journal.exists():
            require(json.loads(journal.read_bytes()).get('phase') in ('complete', 'rolled_back'),
                    'Unfinished deployment requires inspection; no replay')
        source_plan(host, p)
        before = host.live()
        require(before['backend']['release'] == p['expected_backend']
                and before['web']['release'] == p['expected_web']
                and before['database'] == p['expected_database'], 'Current release or database changed')
        current = json.loads((host.state/'CURRENT.json').read_bytes())
        require(current == p['expected_current_bundle'], 'Current bundle registration changed')
        old = host.load(current['bundle_id']); host.verify_artifacts(old)
        if p['target_schema'] == 'V233':
            require(p['candidate']['web'] == old['web'], 'V233 must preserve registered Web artifact')
        delta = p.get('prepared_configuration_delta')
        if delta is None:
            original.guard(old, before, host.clients())
        else:
            require(old['configuration'] == delta['old_configuration']
                and before['configuration'] == delta['prepared_configuration'], 'Prepared configuration differs from fixed evidence')
            original.guard({**old, 'configuration': delta['prepared_configuration']}, before, host.clients())
        require(all(old[k] == before[k] for k in ('backend', 'web')), 'Registered current artifact drift')
        clients = host.clients()
        require(clients == p['active_mini_versions'] and set(clients) <= set(p['supported_mini_versions']),
                'Active client requirements changed')
        require(host.database_raw() == p['expected_ledger'], 'Old complete ledger or idle state changed')
        host.health(p['expected_backend']); host.idle()
        stamps = {k: pointer_stamp(root) for k, root in [('backend', host.root), ('web', host.web)]}
        transaction = {'id': p['bundle_id'], 'format': 'salesbuddy-forward-bundle-v1',
            'plan': plan_ref, 'target': p['bundle_id'], 'before': before, 'active_mini_versions': clients,
            'pointer_before': stamps, 'phase': 'prepared', 'migration_attempted': False,
            'success': False, 'errors': [], 'started_at': datetime.now(timezone.utc).isoformat()}
        owned = host.state/'forward'/p['bundle_id']
        owned.parent.mkdir(parents=True, exist_ok=True)
        owned.mkdir(mode=0o700)  # fixed bundle identity is a once-only reservation
        (host.state/'history').mkdir(parents=True, exist_ok=True)
        require(not (host.state/'history'/(p['bundle_id']+'.json')).exists(), 'History identity already used')
        peer_access(host, p, owned)
        new_json(owned/'INTENT.json', transaction)
        original.atomic_json(journal, transaction)
        def phase(name):
            transaction['phase'] = name
            original.atomic_json(journal, transaction)
            original.atomic_json(owned/'RECEIPT.json', transaction)
        try:
            check_pointers(host, stamps)
            host.stop(); phase('stopped'); host.idle()
            require(host.database_raw() == p['expected_ledger'], 'Ledger changed during drain')
            require(host.clients() == clients, 'Active clients changed during drain')
            require(host.configuration() == before['configuration'], 'Configuration changed during drain')
            source_plan(host, p); check_pointers(host, stamps)
            transaction['backup'] = host.backup(transaction['id']); phase('backed_up')
            require(host.database_raw() == p['expected_ledger'], 'Ledger changed during backup')
            # Conservative irreversible boundary: record before any child can attempt SQL.
            transaction['migration_attempted'] = True; phase('migration_attempted')
            transaction['database_phase'] = database_phase(host, plan_ref, owned/'database')
            observed = transaction['database_phase']
            require(observed['returncode'] == 0 and observed['result'].get('success') is True
                and observed['result'].get('closed') is True and observed['result'].get('errors') == [],
                'Database phase did not complete cleanly')
            phase('migrated_and_permissions_verified')
            expected_configuration = before['configuration']
            if p.get('agent_stage') is not None:
                phase('agent_configuration')
                transaction['agent_stage'] = agent_configuration(host, p, expected_configuration, owned/'agent')
                expected_configuration = transaction['agent_stage']['configuration_after']
                phase('agent_configured')
            new_raw = host.database_raw()
            require(not new_raw['running_jobs'], 'Jobs started during maintenance')
            require(verify_release_ledger(p, migration_plan, new_raw, final=True) == [],
                    'New complete ledger differs')
            require(host.clients() == clients and host.configuration() == expected_configuration,
                    'Clients or configuration changed during maintenance')
            check_pointers(host, stamps); source_plan(host, p)
            phase('registering')
            target = original.make_bundle(host, p['bundle_id'], p['backend'], p['web'],
                p['product_version'], p['supported_mini_versions'], evidence)
            transaction['registered_bundle'] = target; phase('registered')
            original.guard(target, {**before, 'database': host.database(), 'configuration': expected_configuration}, host.clients())
            check_pointers(host, stamps)
            if p['web'] != p['expected_web']:
                phase('switching_web')
                original.switch_pointer(host.web, 'current', p['web'])
                stamps['web'] = pointer_stamp(host.web); phase('web_switched')
            else:
                phase('web_reused')
            check_pointers(host, stamps); phase('switching_backend')
            original.switch_pointer(host.root, 'current', p['backend'])
            stamps['backend'] = pointer_stamp(host.root); phase('pointers_switched')
            check_pointers(host, stamps)
            host.start(); phase('started'); host.health(p['backend'])
            original.guard(target, host.live(), host.clients()); host.verify_artifacts(target)
            host.assert_source_database(p['backend']); check_pointers(host, stamps)
            # previous remains an archival candidate, not authority for schema downgrade.
            original.switch_pointer(host.root, 'previous', p['expected_backend'])
            if p['web'] != p['expected_web']:
                original.switch_pointer(host.web, 'previous', p['expected_web'])
            phase('recording_current'); host.record_current(target)
            transaction['success'] = True; phase('complete')
            new_json(host.state/'history'/(p['bundle_id']+'.json'), transaction)
            return transaction
        except BaseException as exc:
            transaction['success'] = False
            db_receipt = owned/'database/DATABASE.json'
            try:
                if db_receipt.is_file(): transaction['database_receipt'] = file_ref(db_receipt)
            except BaseException as cleanup:
                transaction['errors'].append({'cleanup': 'database_receipt_read', 'type': type(cleanup).__name__})
            transaction['errors'].append(dict(safe_error(exc), phase=transaction['phase']))
            if transaction['migration_attempted']:
                try: host.stop()
                except BaseException as cleanup:
                    transaction['errors'].append({'cleanup': 'stop', 'type': type(cleanup).__name__})
                phase('maintenance_required')
            else:
                try:
                    check_pointers(host, stamps)
                    require(host.database_raw() == p['expected_ledger'] and host.clients() == clients
                        and host.configuration() == before['configuration'], 'Old restart guards changed')
                    host.start(); host.health(p['expected_backend']); phase('rolled_back')
                except BaseException as cleanup:
                    transaction['errors'].append({'cleanup': 'old_services', 'type': type(cleanup).__name__})
                    try: host.stop()
                    except BaseException as stop_error:
                        transaction['errors'].append({'cleanup': 'stop_old', 'type': type(stop_error).__name__})
                    phase('maintenance_required')
            raise


async def ledger(conn):
    raw = await conn.fetchval("""SELECT json_build_object(
      'registered',(SELECT json_agg(version ORDER BY version) FROM ops.schema_migration),
      'checksums',(SELECT json_object_agg(migration_key,checksum_sha256 ORDER BY migration_key) FROM ops.migration_checksum),
      'running_jobs',(SELECT count(*) FROM ops.job WHERE status='running'),
      'checksum_rows',(SELECT json_agg(x ORDER BY migration_key) FROM ops.migration_checksum x),
      'registered_rows',(SELECT json_agg(x ORDER BY version) FROM ops.schema_migration x))::text""")
    return json.loads(raw)


async def database_phase(plan_ref):
    p, plan, _ = inputs(plan_ref)
    folder = Path(plan_ref['path']).parent
    require(folder.name == 'database' and folder.parent.name == p['bundle_id']
            and folder.parent.parent == ROOT/'bundles/forward', 'Fixed owned database receipt directory required')
    require(os.getuid() == pwd.getpwnam('postgres').pw_uid, 'Postgres peer process required')
    result = {'plan': plan_ref, 'success': False, 'closed': False, 'migrations': [], 'errors': []}
    conn = None
    def save(): original.atomic_json(folder/'DATABASE.json', result)
    async def persist(label, value):
        original.safe_id(label)
        result.setdefault('acl_evidence', []).append(new_json(folder/('ACL-'+label+'.json'), value))
        save()
    result['phase'] = 'files_validating'; save()
    try:
        release = original.release_path(ROOT, p['backend'])
        require(Path(__file__).resolve() == release/'backend/deploy/release_forward_bundle.py', 'Candidate caller required')
        migrator = load('_forward_candidate_migrate', release/'database/scripts/migrate.py')
        acl = load('_forward_production_acl', Path(p['acl_source']['path']))
        require(callable(getattr(acl, 'apply_release_permissions', None))
            and callable(getattr(acl, 'verify_release_permissions', None)), 'Production ACL helper not bound')
        acl.load_plan(p['acl_plan']); acl.dependencies()  # fail unreadable/missing SOURCE before the first SQL
        by_key = {m.key: m for m in migrator.discover(release/'database')}
        for row in plan:
            if row['key'] in p['pending']:
                item = by_key[row['key']]
                require(item.checksum == row['sha256'] and item.path.relative_to(release/'database').as_posix() == row['file'],
                        'Exact pending SQL source changed')
    except BaseException as exc:
        result['closed'] = True  # connection was never opened
        result['errors'].append(dict(safe_error(exc), phase=result['phase']))
        save(); return result
    try:
        import asyncpg
        conn = await asyncpg.connect(host='/tmp', database='sales_saas', user='postgres',
            timeout=10, command_timeout=600, server_settings={'application_name': 'salesbuddy-forward-bundle'})
        actual = dict(await conn.fetchrow("SELECT current_database() AS database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid,session_user::text AS session_user,current_user::text AS current_user"))
        acl_plan = document(p['acl_plan'])
        require({k: actual[k] for k in ('database','oid')} == acl_plan['target']
                and actual['session_user'] == actual['current_user'] == 'postgres', 'Production peer identity changed')
        result['identity'] = actual
        require(await conn.fetchval('SELECT pg_try_advisory_lock($1)', migrator.DEPLOY_LOCK), 'Migration lock busy')
        require(not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend')"), 'Other database client present')
        require(not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())"), 'Prepared transaction present')
        result['phase'] = 'connected_preflight'; save()
        before = await ledger(conn); result['before'] = before
        require({k: before[k] for k in ('registered','checksums','running_jobs')} == p['expected_ledger'], 'Fresh database differs')
        require(verify_release_ledger(p, plan, before) == p['pending'], 'Pending migrations differ')
        save()
        for key in p['pending']:
            attempt = {'key': key, 'sha256': by_key[key].checksum, 'commit_outcome': 'unknown'}
            result['phase'] = 'migration_'+key
            result['migrations'].append(attempt); save()  # must precede apply/COMMIT
            require(not conn.is_in_transaction(), 'Per-file commits require no outer transaction')
            state = await migrator.apply(conn, by_key[key], release/'database')
            require(state == 'applied', 'Pending migration was not freshly applied')
            attempt.update(status=state, commit_outcome='acknowledged'); save()
        result['phase'] = 'acl_applying'
        result['acl_commit_outcome'] = 'unknown'; save()
        result['acl_result'] = await acl.apply_release_permissions(conn, plan_ref=p['acl_plan'], persist=persist)
        require(not conn.is_in_transaction(), 'ACL helper left a transaction open')
        require(result['acl_result'].get('commit_attempted') is True
                and result['acl_result'].get('commit_acknowledged') is True
                and result['acl_result'].get('errors') == [] and result['acl_result'].get('cleanup_errors') == [],
                'Production ACL was not committed and verified')
        result['acl_commit_outcome'] = 'acknowledged'; save()
        result['phase'] = 'acl_verifying'; save()
        result['acl_verify'] = await acl.verify_release_permissions(conn, plan_ref=p['acl_plan'])
        require(not conn.is_in_transaction() and result['acl_verify'].get('status') == 'verified_readonly'
                and result['acl_verify'].get('rolled_back') is True and result['acl_verify'].get('committed') is False,
                'ACL verification did not complete readonly rollback')
        save()
        after = await ledger(conn); result['after'] = after
        require(not after['running_jobs'] and verify_release_ledger(p, plan, after, final=True) == [],
                'Final ledger mismatch')
        after_rows = {r['migration_key']: r for r in after['checksum_rows']}
        require(all(after_rows.get(r['migration_key']) == r for r in before['checksum_rows']), 'Old ledger bytes changed')
        registered_rows = {r['version']: r for r in after['registered_rows']}
        require(all(registered_rows.get(r['version']) == r for r in before['registered_rows']), 'Old schema registration rows changed')
        require(all(after_rows[k]['provenance'] == 'executed' for k in p['pending']), 'New migration provenance differs')
        result['success'] = True; result['phase'] = 'complete'
    except BaseException as exc:
        result['errors'].append(dict(safe_error(exc), phase=result['phase']))
        if isinstance(exc, getattr(acl, 'ACLExecutionFailed', ())):
            result['acl_result'] = deepcopy(exc.journal)
            result['acl_commit_outcome'] = ('acknowledged' if exc.journal.get('commit_acknowledged')
                else 'unknown' if exc.journal.get('commit_attempted') else 'not_attempted')
    finally:
        if conn is not None:
            try: await asyncio.wait_for(conn.close(), 10)
            except BaseException as exc: result['errors'].append({'cleanup': 'close', 'type': type(exc).__name__})
            try:
                if not conn.is_closed(): conn.terminate()
                result['closed'] = conn.is_closed()
            except BaseException as exc: result['errors'].append({'cleanup': 'terminate', 'type': type(exc).__name__})
        result['success'] = result['success'] and result['closed'] and not result['errors']
        save()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan'); parser.add_argument('--plan-sha')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--database-phase', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.plan and not args.execute and not args.database_phase:
        print(json.dumps({'source_only': True, 'executed': False})); return 0
    require(args.plan and args.plan_sha, 'Exact plan path and SHA required')
    plan_ref = {'path': args.plan, 'sha256': args.plan_sha}
    inputs(plan_ref)
    def interrupted(signum, frame): raise InterruptedError('Interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    if args.database_phase:
        result = asyncio.run(database_phase(plan_ref))
    elif args.execute:
        require(os.geteuid() == 0, 'Managed forward release requires root')
        result = forward(ManagedForwardHost(), plan_ref)
    else:
        print(json.dumps({'source_only': True, 'executed': False, 'plan_verified': True})); return 0
    print(json.dumps({'success': result['success'], 'phase': result.get('phase'), 'errors': result['errors']}))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'success': False, 'error': safe_error(exc)}))
        raise SystemExit(1) from None
