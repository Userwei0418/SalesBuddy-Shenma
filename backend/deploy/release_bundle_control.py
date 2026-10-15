"""Coordinated managed-host code rollback; database mismatch always fails closed.

Never restores a database in place. A release records the exact live migration
ledger, immutable backend/Web trees, effective Agent metadata and required mini
clients. Use a separate reviewed maintenance plan for schema/data recovery.

Client IDs retain numeric versions (for example 1.2.1). For the 1.2.1 map/catalog
client, also register 1.2.1+mini.<40-lowercase-hex-source-sha> in --clients and
validation.tested_mini_versions. After a compatible bundle is current and before
distributing that build, require-clients --versions must retain every prior ID
and add this exact build ID. The WeChat display version remains 1.2.1. These are
reviewed compatibility declarations, not automatic capability discovery.

adopt-compatibility selects a separately registered compatibility bundle only
when its backend/Web/database/configuration exactly equal the live combination.
Pin both bundle file SHA256 values and the expected current bundle. It changes
only CURRENT.json and its own compatibility receipt; require-clients remains a
separate command. An interrupted adoption requires inspection, never replay.

reconcile-current repairs only a stale CURRENT.json after an independently
verified physical release. Pin both registered bundle file hashes, the stale
current bundle identity and the exact --drift-fields (backend, web, database,
configuration). The target must match every live value and retain active client
support. It writes a separate reconciliation receipt, preserves the physical
transaction and never claims that a physical switch was performed or verified
in the past. Unverifiable or interrupted reconciliation requires inspection.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

from release_upgrade import verify_migration_plan

SERVICES = ('sales-api.service', 'sales-worker.service', 'sales-feishu-worker.service')
LIVE_KEYS = ('backend', 'web', 'database', 'configuration')
SOURCE_PATHS = ('backend/src', 'backend/agent_platform', 'backend/deploy', 'backend/scripts', 'backend/openapi', 'backend/pyproject.toml',
                'backend/uv.lock', 'database')


class Refused(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise Refused('Invalid release identity')
    return value


def validate_client_ids(values):
    """Preserve legacy numeric versions and the explicit 1.2.1 Mini build ID."""
    pattern = r'(?:[0-9]+\.[0-9]+\.[0-9]+|1\.2\.1\+mini\.[0-9a-f]{40})'
    if not values or any(not isinstance(value, str) or not re.fullmatch(pattern, value) for value in values):
        raise Refused('Invalid mini version or build identity')
    return sorted(set(values))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def tree_digest(root, selections):
    files = {}
    for selection in selections:
        selected = root / selection
        paths = sorted(selected.rglob('*')) if selected.is_dir() else [selected]
        for path in paths:
            if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                raise Refused('Release source contains a symlink')
            if path.is_file():
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise Refused('Empty release source')
    return digest(files)


def release_path(root, name):
    path = root / 'releases' / safe_id(name)
    if not path.is_dir() or path.is_symlink() or path.resolve().parent != (root / 'releases').resolve():
        raise Refused('Release must be an existing direct immutable directory')
    return path


def pointer(root, name='current'):
    path = root / name
    if not path.is_symlink():
        raise Refused('Missing release pointer')
    actual = path.resolve(strict=True)
    if actual != release_path(root, actual.name):
        raise Refused('Pointer escapes release root')
    return actual.name


def switch_pointer(root, name, target):
    destination = release_path(root, target)
    temporary = root / ('.' + name + '.' + uuid.uuid4().hex)
    temporary.symlink_to(destination)
    try:
        os.replace(temporary, root / name)
        fd = os.open(root, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def guard(target, live, clients):
    if target['database'] != live['database']:
        raise Refused('DATABASE_MISMATCH: no code-only switch; prepare reviewed database recovery or forward repair')
    if target['configuration'] != live['configuration']:
        raise Refused('CONFIGURATION_MISMATCH: reconcile effective configuration before switching')
    if not set(clients) <= set(target['supported_mini_versions']):
        raise Refused('CLIENT_MISMATCH: target cannot serve all active mini-program versions')


class Host:
    def __init__(self, root=Path('/www/wwwroot/salegent'), web=Path('/var/www/salesbuddy-web')):
        self.root, self.web = root, web
        self.state = root / 'bundles'
        self.pg = Path('/www/server/pgsql/bin')

    def run(self, arguments, **kwargs):
        result = subprocess.run([str(a) for a in arguments], capture_output=True, text=True,
                                timeout=kwargs.pop('timeout', 120), **kwargs)
        if result.returncode:
            # Never print captured service/configuration/SQL values on error.
            raise Refused('Host command failed: ' + Path(str(arguments[0])).name)
        return result.stdout.strip()

    @contextmanager
    def lock(self):
        with (self.root / '.deploy.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('Another deployment or retention operation is active') from None
            yield

    def database_raw(self):
        sql = """SELECT json_build_object(
          'registered',(SELECT json_agg(version ORDER BY version) FROM ops.schema_migration),
          'checksums',(SELECT json_object_agg(migration_key,checksum_sha256 ORDER BY migration_key) FROM ops.migration_checksum),
          'running_jobs',(SELECT count(*) FROM ops.job WHERE status='running'))"""
        env = dict(os.environ, PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=10000')
        return json.loads(self.run(['runuser', '-u', 'postgres', '--', self.pg/'psql',
            '-h', '/tmp', '-d', 'sales_saas', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', sql], env=env))

    def database(self):
        value = self.database_raw()
        return {'name': 'sales_saas', 'schema': max(value['registered']),
                'ledger_sha256': digest({'registered': value['registered'], 'checksums': value['checksums']})}

    def configuration(self):
        properties = ('User', 'Group', 'WorkingDirectory', 'EnvironmentFiles', 'FragmentPath', 'DropInPaths')
        metadata = {}
        for service in SERVICES:
            values = dict(line.split('=', 1) for line in self.run(
                ['systemctl', 'show', service, *['-p'+p for p in properties]]).splitlines() if '=' in line)
            paths = set(re.findall(r'/[^\s;()]+', ' '.join(values.get(p, '') for p in
                ('EnvironmentFiles', 'FragmentPath', 'DropInPaths'))))
            # Metadata only: no environment/credential file content is opened.
            stamps = {}
            for path in sorted(paths):
                stat = Path(path).stat()
                stamps[path] = [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_mode]
            metadata[service] = {'properties': values, 'file_metadata': stamps}
        current = self.root / 'current'
        # Product archives need not carry operational tooling. Use the reviewed
        # controller directory for probes, but import Settings from live code.
        control_directory = Path(__file__).resolve().parent
        probe = control_directory/'verify_agent_runtime.py'
        inherited = control_directory/'inherit_service_environment.py'
        if not probe.is_file() or not inherited.is_file():
            raise Refused('Reviewed control directory lacks required configuration probes')
        invoke_probe = ('import runpy,sys; sys.path[:0]=[sys.argv[1],sys.argv[2]]; '
                        'runpy.run_path(sys.argv[3],run_name="__main__")')
        reports = []
        for service in SERVICES[:2]:
            reports.append(json.loads(self.run([current/'.venv/bin/python',
                inherited, '--service', service,
                '--unit', 'sb-bundle-config-'+uuid.uuid4().hex[:12], '--', current/'.venv/bin/python',
                '-c', invoke_probe, current/'backend/src', control_directory, probe])))
        if reports[0] != reports[1]:
            raise Refused('API and Worker Agent metadata differ')
        return {'service_metadata_sha256': digest(metadata), 'agent_metadata': reports[0]}

    def artifacts(self, backend, web):
        source, static = release_path(self.root, backend), release_path(self.web, web)
        revision = (source/'REVISION').read_text().strip()
        if not re.fullmatch(r'[a-f0-9]{40}', revision): raise Refused('Invalid backend revision')
        return {'backend': {'release': backend, 'revision': revision, 'sha256': tree_digest(source, SOURCE_PATHS)},
                'web': {'release': web, 'sha256': tree_digest(static, ('web',))}}

    def live(self):
        return {**self.artifacts(pointer(self.root), pointer(self.web)),
                'database': self.database(), 'configuration': self.configuration()}

    def assert_source_database(self, backend):
        source = release_path(self.root, backend)
        plan = json.loads(self.run([source/'.venv/bin/python', source/'database/scripts/migrate.py', '--plan']))
        database = self.database_raw()
        schema = max(database['registered'])
        verify_migration_plan(plan, database['checksums'], set(database['registered']), schema, schema)

    def health(self, backend):
        expected = release_path(self.root, backend)
        last = None
        for _ in range(20):
            try:
                for service in SERVICES:
                    values = dict(line.split('=', 1) for line in self.run(
                        ['systemctl','show',service,'-pMainPID','-pActiveState']).splitlines())
                    if values['ActiveState'] != 'active' or Path('/proc/'+values['MainPID']+'/cwd').resolve() != expected:
                        raise Refused('Service is not running target code')
                for endpoint in ('live','ready'):
                    value = json.loads(self.run(['curl','-fsS','--max-time','5',
                        'https://www.ericepc.com/api/v1/health/'+endpoint]))
                    if value.get('status') != 'ok': raise Refused('Health check failed')
                version = json.loads(self.run(['curl','-fsS','--max-time','5',
                    'https://www.ericepc.com/api/v1/health/version']))
                if version.get('revision') != (expected/'REVISION').read_text().strip():
                    raise Refused('Public API revision differs from target')
                return
            except (Refused, ValueError, OSError) as exc:
                last = exc; time.sleep(1)
        raise Refused('Target service health/revision did not converge') from last

    def stop(self):
        self.run(['systemctl','stop',SERVICES[0]])
        self.run(['systemctl','stop',*SERVICES[1:]])

    def start(self):
        self.run(['systemctl','start',*SERVICES])

    def idle(self):
        if self.database_raw()['running_jobs']:
            raise Refused('Running jobs must finish before switching')

    def backup(self, transaction_id):
        directory = Path('/var/backups/sales-management') / ('bundle-'+transaction_id)
        directory.mkdir(mode=0o700)
        partial, final = directory/'pre-switch.dump.partial', directory/'pre-switch.dump'
        loader = SourceFileLoader('bundle_database_backup', str(Path(__file__).with_name('sales-db-backup')))
        module = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        required = 8 * 1024**3
        module.check_free_space(directory, required)
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                module.export_database(['runuser','-u','postgres','--',str(self.pg/'pg_dump'),
                    '-h','/tmp','-Fc','sales_saas'], stream, directory, required, 3300)
                stream.flush(); os.fsync(stream.fileno())
            if partial.stat().st_size == 0: raise Refused('Empty backup')
            self.run([self.pg/'pg_restore','--list',partial])
            os.replace(partial, final)
            return {'path': str(final), 'bytes': final.stat().st_size, 'archive_catalog_verified': True,
                    'restore_drill': 'not_performed_on_production_backup'}
        finally:
            partial.unlink(missing_ok=True)

    def clients(self):
        return json.loads((self.state/'ACTIVE_MINI_CLIENTS.json').read_text())['versions']

    def load(self, name):
        value = json.loads((self.state/(safe_id(name)+'.json')).read_text())
        if value.get('format') != 'salesbuddy-release-bundle-v1': raise Refused('Unknown bundle format')
        if value['bundle_id'] != name: raise Refused('Bundle identity mismatch')
        return value

    def verify_artifacts(self, bundle):
        actual = self.artifacts(bundle['backend']['release'], bundle['web']['release'])
        if any(actual[key] != bundle[key] for key in ('backend','web')):
            raise Refused('Immutable backend/Web artifact changed')

    def record_current(self, bundle):
        atomic_json(self.state/'CURRENT.json', {'bundle_id': bundle['bundle_id']})
        self.record_inventory()

    def record_inventory(self):
        from runtime_inventory import inventory
        atomic_json(self.root/'RELEASES.json', inventory(self.root))


def make_bundle(host, name, backend, web, product, clients, evidence):
    clients = validate_client_ids(clients)
    host.assert_source_database(backend)
    value = {**host.artifacts(backend, web), 'database':host.database(),
             'configuration':host.configuration(), 'format':'salesbuddy-release-bundle-v1',
             'bundle_id':safe_id(name), 'product_version':product, 'supported_mini_versions':sorted(set(clients)),
             'created_at':datetime.now(timezone.utc).isoformat(), 'database_action':'preserve_exact_ledger',
             'validation':evidence}
    if evidence.get('status') != 'passed' or evidence.get('tested_source_sha256') != value['backend']['sha256']:
        raise Refused('Validation evidence does not cover candidate source')
    if not set(clients) <= set(evidence.get('tested_mini_versions', [])):
        raise Refused('Missing mini-program compatibility evidence')
    path = host.state/(name+'.json')
    if path.exists(): raise Refused('Bundle already registered; identities are immutable')
    atomic_json(path, value)
    # The existing retention tool protects root-level release pointers.
    switch_pointer(host.root, 'bundle-'+name, backend)
    switch_pointer(host.web, 'bundle-'+name, web)
    return value


def plan(host, name):
    target = host.load(name)
    host.verify_artifacts(target)
    current = host.live()
    guard(target, current, host.clients())
    host.assert_source_database(target['backend']['release'])
    return target, current


def assert_metadata_operations_finished(host):
    """A partial metadata operation must not be hidden by another release."""
    for name in ('compatibility', 'reconciliations'):
        folder = host.state/name
        if host.state.is_symlink() or folder.is_symlink():
            raise Refused('Bundle metadata directory must not be a symlink')
        for path in folder.glob('*.json'):
            if path.is_symlink() or json.loads(path.read_bytes()).get('phase') != 'complete':
                raise Refused('Unfinished bundle metadata operation requires inspection')


def adopt_compatibility(host, name, expected_current, target_sha256, current_sha256):
    """Adopt reviewed client evidence without a physical release or service action."""
    return _adopt_current_metadata(host, name, expected_current, target_sha256, current_sha256)


def reconcile_current(host, name, expected_current, target_sha256, current_sha256, drift_fields):
    """Repair only the current ledger; never replay or fabricate a release."""
    declared = set(drift_fields)
    if not declared or not declared <= set(LIVE_KEYS) or len(declared) != len(drift_fields):
        raise Refused('Declare the exact distinct live drift fields')
    return _adopt_current_metadata(host, name, expected_current, target_sha256, current_sha256,
                                   declared_drift=declared)


def _adopt_current_metadata(host, name, expected_current, target_sha256, current_sha256, declared_drift=None):
    reconciliation = declared_drift is not None
    operation = 'Reconciliation' if reconciliation else 'Compatibility adoption'
    name, expected_current = safe_id(name), safe_id(expected_current)
    if name == expected_current:
        raise Refused(operation + ' requires a new immutable bundle identity')
    if any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in (target_sha256, current_sha256)):
        raise Refused('Both immutable bundle file SHA256 values are required')
    with host.lock():
        current_path, journal_path = host.state/'CURRENT.json', host.state/'TRANSACTION.json'
        folder = host.state/('reconciliations' if reconciliation else 'compatibility')
        receipt_path = folder/(name+'.json')
        if receipt_path.exists() or receipt_path.is_symlink():
            raise Refused(operation + ' already attempted; inspect its receipt')
        assert_metadata_operations_finished(host)
        paths = [current_path, journal_path, host.state/'ACTIVE_MINI_CLIENTS.json',
                 host.state/(expected_current+'.json'), host.state/(name+'.json')]
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise Refused('Expected regular current, transaction, client and bundle metadata files')
        original = {path: path.read_bytes() for path in paths}
        if json.loads(original[current_path]) != {'bundle_id': expected_current}:
            raise Refused('Current bundle changed; refresh metadata operation plan')
        if json.loads(original[journal_path]).get('phase') not in ('complete', 'rolled_back'):
            raise Refused('Unfinished switch requires inspection before metadata operation')
        for bundle_id, expected_sha in ((name, target_sha256), (expected_current, current_sha256)):
            if hashlib.sha256(original[host.state/(bundle_id+'.json')]).hexdigest() != expected_sha:
                raise Refused('Immutable bundle file differs from reviewed SHA256')

        def pointer_stamps():
            result = {}
            for key, root in (('backend', host.root), ('web', host.web)):
                for which in ('current', 'previous'):
                    path = root/which
                    if which == 'previous' and not path.exists() and not path.is_symlink():
                        result[key+'_'+which] = None
                        continue
                    release = pointer(root, which)
                    info = path.lstat()
                    result[key+'_'+which] = [release, os.readlink(path), info.st_ino, info.st_mtime_ns]
            return result

        pointers = pointer_stamps()
        clients = validate_client_ids(host.clients())
        current = host.load(expected_current)
        target, live = plan(host, name)  # Original artifact, full ledger, config and client guards.
        if any(target[key] != live[key] for key in LIVE_KEYS):
            raise Refused(operation + ' requires target exactly equal to the live combination')
        actual_drift = {key for key in LIVE_KEYS if current[key] != live[key]}
        if reconciliation:
            if actual_drift != declared_drift:
                raise Refused('Declared drift does not equal the actual stale ledger fields')
        elif actual_drift:
            raise Refused('Compatibility adoption requires exactly the current live combination')
        if target['product_version'] != current['product_version'] or target.get('database_action') != 'preserve_exact_ledger':
            raise Refused(operation + ' cannot change product or database policy')
        if not reconciliation:
            guard(current, live, clients)
        supported = validate_client_ids(target['supported_mini_versions'])
        evidence = target.get('validation')
        if not isinstance(evidence, dict) or evidence.get('status') != 'passed' or evidence.get('tested_source_sha256') != live['backend']['sha256']:
            raise Refused('Validation evidence does not cover the live source')
        if not set(supported) <= set(validate_client_ids(evidence.get('tested_mini_versions', []))):
            raise Refused('Missing exact client compatibility evidence')
        host.health(live['backend']['release'])
        if host.live() != live:
            raise Refused('Live artifacts, complete ledger or configuration changed during metadata checks')

        def unchanged(include_current):
            return (pointer_stamps() == pointers and all(
                path.is_file() and not path.is_symlink() and path.read_bytes() == raw
                for path, raw in original.items() if include_current or path != current_path))

        if not unchanged(True):
            raise Refused('Current metadata, clients, transaction or runtime pointers changed')
        receipt = {'format': ('salesbuddy-current-reconciliation-v1' if reconciliation else 'salesbuddy-compatibility-adoption-v1'), 'target': name,
                   'previous_bundle': expected_current, 'phase': 'prepared',
                   'started_at': datetime.now(timezone.utc).isoformat(),
                   'current_bundle_sha256': current_sha256, 'target_bundle_sha256': target_sha256,
                   'physical_transaction_sha256': hashlib.sha256(original[journal_path]).hexdigest(),
                   'active_mini_versions': clients, 'live': live, 'runtime_pointers': pointers,
                   'changes': ['bundles/CURRENT.json', 'bundles/'+folder.name+'/'+name+'.json']}
        if reconciliation:
            receipt.update(declared_drift=sorted(declared_drift),
                           stale_recorded_state={key: current[key] for key in LIVE_KEYS},
                           historical_switch_status='not_reconstructed',
                           runtime_action='none')
        atomic_json(receipt_path, receipt)
        try:
            # Preparing the receipt may block on storage; reject a changed state
            # again immediately before replacing the ledger, without guessing it.
            if not unchanged(True) or host.live() != live:
                raise Refused('Live state or metadata changed after receipt preparation')
            atomic_json(current_path, {'bundle_id': name})
            if json.loads(current_path.read_bytes()) != {'bundle_id': name} or not unchanged(False) or host.live() != live:
                raise Refused('Bundle metadata readback failed; preserve actual state')
            receipt.update(phase='complete', finished_at=datetime.now(timezone.utc).isoformat())
            atomic_json(receipt_path, receipt)
            return receipt
        except BaseException as error:
            receipt.update(phase='needs_inspection', error_type=type(error).__name__)
            try:
                atomic_json(receipt_path, receipt)
            except OSError:
                pass  # The durable prepared receipt still marks an unresolved attempt.
            raise


def switch(host, name, expected_backend, expected_web):
    with host.lock():
        journal_path = host.state/'TRANSACTION.json'
        assert_metadata_operations_finished(host)
        if journal_path.exists() and json.loads(journal_path.read_text()).get('phase') not in ('complete','rolled_back'):
            raise Refused('Unfinished switch; inspect TRANSACTION.json and recover first')
        target, before = plan(host, name)
        if before['backend']['release'] != expected_backend or before['web']['release'] != expected_web:
            raise Refused('Expected current release changed; refresh release plan')
        old_record = host.state/'CURRENT.json'
        previous_bundle = None
        previous_bundle_sha256 = None
        if old_record.exists() or old_record.is_symlink():
            if old_record.is_symlink() or not old_record.is_file():
                raise Refused('Current bundle metadata must be a regular file')
            previous_bundle = json.loads(old_record.read_bytes())
            previous = host.load(previous_bundle['bundle_id'])
            if any(previous[key] != before[key] for key in LIVE_KEYS):
                raise Refused('Current bundle drift; reconcile the ledger before switching')
            guard(previous, before, host.clients())
            previous_bundle_sha256 = hashlib.sha256(
                (host.state/(previous['bundle_id']+'.json')).read_bytes()).hexdigest()
        host.health(expected_backend); host.idle()
        transaction = {'id':datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8],
                       'target':name,'before':before,'active_mini_versions':host.clients(),'phase':'prepared'}
        transaction['previous_pointers'] = {key:pointer(root,'previous') if (root/'previous').exists() else None
                                            for key,root in [('backend',host.root),('web',host.web)]}
        transaction['previous_bundle'] = previous_bundle
        transaction['previous_bundle_sha256'] = previous_bundle_sha256
        atomic_json(journal_path, transaction)
        try:
            host.stop(); transaction['phase']='stopped'; atomic_json(journal_path,transaction)
            host.idle()
            guard(target, host.live(), host.clients())
            transaction['backup']=host.backup(transaction['id'])
            transaction['phase']='switching'; atomic_json(journal_path,transaction)
            switch_pointer(host.web,'current',target['web']['release'])
            switch_pointer(host.root,'current',target['backend']['release'])
            host.start(); host.health(target['backend']['release'])
            guard(target,host.live(),host.clients())
            host.verify_artifacts(target)
            switch_pointer(host.root,'previous',expected_backend)
            switch_pointer(host.web,'previous',expected_web)
            host.record_current(target)
            transaction['phase']='complete'; atomic_json(journal_path,transaction)
            atomic_json(host.state/'history'/(transaction['id']+'.json'),transaction)
            return transaction
        except BaseException:
            recover(host, transaction, journal_path)
            raise


def recover(host, transaction, journal_path):
    """Same-database interrupted switch recovery, never destructive DB restore."""
    before = transaction['before']
    transaction['phase']='recovering'; atomic_json(journal_path,transaction)
    try:
        assert_metadata_operations_finished(host)
        if host.clients() != transaction['active_mini_versions']:
            raise Refused('Client release changed during switch; automatic recovery refused')
        live = host.live()
        if before['database'] != live['database'] or before['configuration'] != live['configuration']:
            raise Refused('Database/configuration changed; manual coordinated recovery required')
        host.verify_artifacts(before)
        previous = None
        if transaction.get('previous_bundle'):
            previous = host.load(transaction['previous_bundle']['bundle_id'])
            if any(previous[key] != before[key] for key in LIVE_KEYS):
                raise Refused('Previous bundle drift; cannot restore stale current metadata')
            guard(previous, before, host.clients())
            expected_sha = transaction.get('previous_bundle_sha256')
            if expected_sha and hashlib.sha256((host.state/(previous['bundle_id']+'.json')).read_bytes()).hexdigest() != expected_sha:
                raise Refused('Previous registered bundle changed during switch')
        host.stop()
        switch_pointer(host.web,'current',before['web']['release'])
        switch_pointer(host.root,'current',before['backend']['release'])
        host.start(); host.health(before['backend']['release'])
        for key, root in [('backend',host.root),('web',host.web)]:
            old = transaction.get('previous_pointers', {}).get(key)
            if old: switch_pointer(root,'previous',old)
        if transaction['previous_bundle']:
            host.record_current(previous)
        else:
            (host.state/'CURRENT.json').unlink(missing_ok=True)
            host.record_inventory()
        transaction['phase']='rolled_back'; atomic_json(journal_path,transaction)
        atomic_json(host.state/'history'/(transaction['id']+'.json'),transaction)
    except BaseException:
        transaction['phase']='recovery_required'; atomic_json(journal_path,transaction)
        raise Refused('Recovery requires attention; see durable transaction record') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('inspect')
    for action in ('init-clients','require-clients'):
        sub.add_parser(action).add_argument('--versions',nargs='+',required=True)
    seal=sub.add_parser('register')
    for option in ('id','backend','web','product','evidence'):
        seal.add_argument('--'+option,required=True)
    seal.add_argument('--clients',nargs='+',required=True)
    sub.add_parser('plan').add_argument('--target',required=True)
    adoption=sub.add_parser('adopt-compatibility')
    for option in ('target','expected-current','target-sha256','current-sha256'):
        adoption.add_argument('--'+option,required=True)
    reconciliation=sub.add_parser('reconcile-current')
    for option in ('target','expected-current','target-sha256','current-sha256'):
        reconciliation.add_argument('--'+option,required=True)
    reconciliation.add_argument('--drift-fields', nargs='+', choices=LIVE_KEYS, required=True)
    change=sub.add_parser('switch'); change.add_argument('--target',required=True)
    change.add_argument('--expected-backend',required=True); change.add_argument('--expected-web',required=True)
    sub.add_parser('recover')
    args=parser.parse_args()
    if args.command in ('init-clients','require-clients'):
        args.versions = validate_client_ids(args.versions)
    elif args.command == 'register':
        args.clients = validate_client_ids(args.clients)
    host=Host()
    if os.geteuid()!=0: raise Refused('Managed host operations require root')
    def interrupted(signum, frame): raise InterruptedError('Release operation interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    if args.command=='inspect': result=host.live()
    elif args.command=='plan':
        target,before=plan(host,args.target)
        result={'status':'compatible','target':target['bundle_id'],'database_action':'preserve_exact_ledger',
                'before':before,'target_bundle':target}
    elif args.command=='switch': result=switch(host,args.target,args.expected_backend,args.expected_web)
    elif args.command=='adopt-compatibility':
        result=adopt_compatibility(host,args.target,args.expected_current,args.target_sha256,args.current_sha256)
    elif args.command=='reconcile-current':
        result=reconcile_current(host,args.target,args.expected_current,args.target_sha256,args.current_sha256,
                                 args.drift_fields)
    else:
        with host.lock():
            if args.command in ('init-clients','require-clients'):
                path=host.state/'ACTIVE_MINI_CLIENTS.json'
                if args.command=='init-clients':
                    if path.exists(): raise Refused('Client requirements already initialized')
                else:
                    assert_metadata_operations_finished(host)
                    if not set(host.clients()) <= set(args.versions): raise Refused('Cannot silently remove active clients')
                    current=json.loads((host.state/'CURRENT.json').read_text())
                    bundle=host.load(current['bundle_id']); host.verify_artifacts(bundle)
                    live=host.live(); guard(bundle,live,args.versions)
                    if any(bundle[key]!=live[key] for key in ('backend','web')): raise Refused('Current bundle drift')
                result={'versions':sorted(set(args.versions))}; atomic_json(path,result)
            elif args.command=='register':
                result=make_bundle(host,args.id,args.backend,args.web,args.product,args.clients,
                                   json.loads(Path(args.evidence).read_text()))
            else:
                path=host.state/'TRANSACTION.json'; transaction=json.loads(path.read_text())
                if transaction['phase'] in ('complete','rolled_back'): raise Refused('No interrupted switch to recover')
                recover(host,transaction,path); result=transaction
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except (Refused,ValueError,OSError,subprocess.SubprocessError) as error:
        # No raw subprocess outputs, SQL, settings or secrets are displayed.
        print(json.dumps({'status':'refused','reason':str(error) if isinstance(error,Refused) else type(error).__name__}))
        raise SystemExit(1) from None
