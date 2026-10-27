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


def knowledge_contract_for(plan):
    """Return the exact incremental release contract for a plan scope."""
    scope = plan.get('release_scope')
    if scope == ['release_1.2.2_knowledge_distribution']:
        import release122_knowledge_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_labels']:
        import release122_knowledge_labels_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_image_preview']:
        import release122_knowledge_image_preview_contract as contract
        return contract
    if scope == ['release_1.2.2_knowledge_silent_publication']:
        import release122_knowledge_silent_publication_contract as contract
        return contract
    if is_visit_index(plan):
        import release122_visit_index_contract as contract
        return contract
    if is_fde_history(plan):
        import release123_contract as contract
        return contract
    if is_follow_up_recency(plan):
        import release124_recency_contract as contract
        return contract
    if is_asr_recovery(plan):
        import release125_asr_contract as contract
        return contract
    if is_battlemap_report(plan):
        import release_battlemap_report_contract as contract
        return contract
    if is_opportunity_claim(plan):
        import release_opportunity_claim_contract as contract
        return contract
    if is_dashboard_targets(plan):
        import release_dashboard_targets_contract as contract
        return contract
    if is_customer_followup(plan):
        import release_customer_followup_contract as contract
        return contract
    if is_business_fixes(plan):
        import release1210_business_contract as contract
        return contract
    if is_task_lightweight(plan):
        import release128_task_contract as contract
        return contract
    if is_task_execution(plan):
        import release126_task_contract as contract
        return contract
    return None


def is_battlemap_report(plan):
    return plan.get('release_scope') == ['release_1.2.14_battlemap_report']


def is_opportunity_claim(plan):
    return plan.get('release_scope') == ['release_1.2.12_opportunity_claim']


def is_dashboard_targets(plan):
    return plan.get('release_scope') == ['release_1.2.10_dashboard_targets']


def is_customer_followup(plan):
    return plan.get('release_scope') == ['release_1.2.10_customer_follow_up']


def is_business_fixes(plan):
    return plan.get('release_scope') == ['release_1.2.10_business_fixes']


def is_task_lightweight(plan):
    return plan.get('release_scope') == ['release_1.2.8_task_lightweight']


def is_task_execution(plan):
    return plan.get('release_scope') == ['release_1.2.6_task_execution'] or is_task_lightweight(plan)


def is_asr_recovery(plan):
    return plan.get('release_scope') == ['release_1.2.5_asr_recovery']


def is_visit_index(plan):
    return plan.get('release_scope') == ['release_1.2.2_visit_field_prefix_index']


def is_fde_history(plan):
    return plan.get('release_scope') == ['release_1.2.3_fde_members_history']


def is_follow_up_recency(plan):
    return plan.get('release_scope') == ['release_1.2.4_follow_up_recency']


def verify_release_ledger(p, migrations, observed, *, final=False):
    """Keep the historical-migration exception bound at every ledger check."""
    contract_id = None
    knowledge_contract = knowledge_contract_for(p)
    if knowledge_contract:
        knowledge_contract.validate(p, migrations, digest=original.digest, require=require)
        pending = [row['key'] for row in migrations if row['key'] not in observed['checksums']]
        knowledge_contract.validate_migrations(migrations, observed['checksums'], observed['registered'],
            p['target_schema'] if final else p['source_schema'], p['target_schema'], pending,
            digest=original.digest, require=require)
    elif p.get('release_scope') == ['release_1.2.2']:
        from release122_contract import validate, validate_migrations
        validate(p, migrations, digest=original.digest, require=require)
        pending = [row['key'] for row in migrations if row['key'] not in observed['checksums']]
        validate_migrations(migrations, observed['checksums'], observed['registered'],
            p['target_schema'] if final else p['source_schema'], p['target_schema'], pending,
            digest=original.digest, require=require)
    elif p.get('release_scope') == ['review_okr_1.2.1']:
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
    release122 = p['product_version'] == '1.2.2' and p.get('release_scope') == ['release_1.2.2']
    knowledge122 = p['product_version'] == '1.2.2' and p.get('release_scope') == ['release_1.2.2_knowledge_distribution']
    knowledge_labels = p['product_version'] == '1.2.2' and p.get('release_scope') == ['release_1.2.2_knowledge_labels']
    knowledge_image_preview = (p['product_version'] == '1.2.2'
                               and p.get('release_scope') in (['release_1.2.2_knowledge_image_preview'],
                                                            ['release_1.2.2_knowledge_silent_publication'],
                                                            ['release_1.2.2_visit_field_prefix_index']))
    fde_history = (p['product_version'] == '1.2.3' and is_fde_history(p)
                   and (p['source_schema'], p['target_schema']) == ('V279', 'V280'))
    follow_up_recency = (p['product_version'] == '1.2.4' and is_follow_up_recency(p)
                        and (p['source_schema'], p['target_schema']) == ('V280', 'V281'))
    asr_recovery = (p['product_version'] == '1.2.5' and is_asr_recovery(p)
                    and (p['source_schema'], p['target_schema']) == ('V281', 'V284'))
    task_execution = ((p['product_version'] == '1.2.6'
                       and p.get('release_scope') == ['release_1.2.6_task_execution']
                       and (p['source_schema'], p['target_schema']) == ('V284', 'V286'))
                      or (p['product_version'] == '1.2.8' and is_task_lightweight(p)
                          and (p['source_schema'], p['target_schema']) == ('V286', 'V288')))
    business_fixes = (p['product_version'] == '1.2.10' and is_business_fixes(p)
                      and (p['source_schema'], p['target_schema']) == ('V288', 'V292'))
    battlemap_report = (p['product_version'] == '1.2.14' and is_battlemap_report(p)
                        and (p['source_schema'], p['target_schema']) == ('V297', 'V298'))
    opportunity_claim = (p['product_version'] == '1.2.12' and is_opportunity_claim(p)
                         and (p['source_schema'], p['target_schema']) == ('V296', 'V297'))
    dashboard_targets = (p['product_version'] == '1.2.10' and is_dashboard_targets(p)
                         and (p['source_schema'],p['target_schema']) == ('V293','V296'))
    customer_followup = (p['product_version'] == '1.2.10' and is_customer_followup(p)
                         and (p['source_schema'], p['target_schema']) == ('V292', 'V293'))
    require(battlemap_report or opportunity_claim or dashboard_targets or customer_followup or business_fixes or task_execution or asr_recovery or follow_up_recency or fde_history or knowledge122 or knowledge_labels or knowledge_image_preview or release122 or release121 or identity239 or review_okr or (p['product_version'] == '1.2.0' and (
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
    if battlemap_report or opportunity_claim or dashboard_targets or customer_followup or business_fixes or task_execution or asr_recovery or follow_up_recency or fde_history or knowledge122 or knowledge_labels or knowledge_image_preview:
        knowledge_contract_for(p).validate(p, migration_plan, digest=original.digest, require=require)
    if release122:
        from release122_contract import validate
        validate(p, migration_plan, digest=original.digest, require=require)
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
    if battlemap_report or opportunity_claim or dashboard_targets or customer_followup or business_fixes or task_execution or asr_recovery or follow_up_recency or fde_history or knowledge122 or knowledge_labels or knowledge_image_preview or release122:
        acl_plan = document(p['acl_plan'])
        contract = knowledge_contract_for(p) if battlemap_report or opportunity_claim or dashboard_targets or customer_followup or business_fixes or task_execution or asr_recovery or follow_up_recency or fde_history or knowledge122 or knowledge_labels or knowledge_image_preview else None
        if contract is None:
            from release122_contract import GATE_FILES, validate_acl
        else:
            GATE_FILES, validate_acl = contract.GATE_FILES, contract.validate_acl
        validate_acl(acl_plan, document(acl_plan['policy_ref']), digest=original.digest, require=require)
        for name in GATE_FILES:
            source = ROOT/'releases'/p['backend']/'backend/deploy'/name
            require(source.is_file() and not source.is_symlink()
                    and acl_plan.get('source_files', {}).get(str(source))
                        == hashlib.sha256(source.read_bytes()).hexdigest(),
                    '1.2.2 release gates must be fixed by the reviewed ACL source closure')
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
    acl_name = 'release_visit_index_security.py' if is_visit_index(p) else 'release120_production_acl.py'
    if fde_history:
        acl_name = 'release123_fde_security.py'
    if follow_up_recency:
        acl_name = 'release124_recency_security.py'
    if asr_recovery:
        acl_name = 'release125_asr_security.py'
    if task_execution:
        acl_name = 'release128_task_security.py' if is_task_lightweight(p) else 'release126_task_security.py'
    if business_fixes:
        acl_name = 'release1210_business_security.py'
    if customer_followup:
        acl_name = 'release_customer_followup_security.py'
    if battlemap_report:
        acl_name = 'release_battlemap_report_security.py'
    if opportunity_claim:
        acl_name = 'release_opportunity_claim_security.py'
    if dashboard_targets:
        acl_name = 'release_dashboard_targets_security.py'
    require(Path(p['acl_source']['path']) == ROOT/'releases'/p['backend']/'backend/deploy'/acl_name,
            'ACL helper must belong to candidate deploy directory')
    if battlemap_report or opportunity_claim or dashboard_targets or business_fixes or customer_followup:
        original.validate_bundle_evidence(evidence, product=p['product_version'],
            backend=p['candidate']['backend'], web=p['candidate']['web'], clients=p['supported_mini_versions'])
    else:
        require(evidence.get('status') == 'passed'
                and evidence.get('tested_source_sha256') == p['candidate']['backend']['sha256'],
                'Validation must cover final managed source')
        require(set(p['supported_mini_versions']) <= set(evidence.get('tested_mini_versions', [])),
                'Compatibility evidence missing')
    pending = verify_release_ledger(p, migration_plan, p['expected_ledger'])
    require(pending == p['pending'] and pending, 'Exact nonempty forward migration set required')
    if p.get('release_scope') == ['release_1.2.2_knowledge_silent_publication'] or is_visit_index(p) or fde_history or follow_up_recency or asr_recovery or task_execution or business_fixes or customer_followup or dashboard_targets or opportunity_claim or battlemap_report:
        validated_acl(p)  # Resolve actual frozen dependencies before stopping services.
    return p, migration_plan, evidence


def validated_acl(p):
    acl = load('_forward_production_acl', Path(p['acl_source']['path']))
    if is_fde_history(p) or is_follow_up_recency(p) or is_asr_recovery(p) or is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
        require(all(callable(getattr(acl, name, None)) for name in
                    ('verify_state', 'grant_execute', 'verify_release_permissions'))
                and not hasattr(acl, 'apply_release_permissions'), 'Atomic function security helper required')
        acl_plan, _ = acl.load_plan(p['acl_plan'])
        return acl, acl_plan
    if is_visit_index(p):
        require(callable(getattr(acl, 'verify_preserved_permissions', None))
                and not hasattr(acl, 'apply_release_permissions'), 'Readonly index security helper required')
        acl_plan, _ = acl.load_plan(p['acl_plan'])
        return acl, acl_plan
    require(callable(getattr(acl, 'apply_release_permissions', None))
        and callable(getattr(acl, 'verify_release_permissions', None)), 'Production ACL helper not bound')
    acl_plan, _ = acl.load_plan(p['acl_plan'])
    if knowledge_contract_for(p) is not None or p.get('release_scope') == ['release_1.2.2']:
        acl.dependencies(acl_plan)
    else:
        acl.dependencies()
    return acl, acl_plan


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
    """Validate every database-phase input as its OS user before stopping services."""
    account = pwd.getpwnam('postgres')
    os.chown(owned, 0, account.pw_gid)
    os.chmod(owned, 0o710)  # root owner, postgres traversal only, no global access
    release = original.release_path(host.root, p['backend'])
    acl_plan = document(p['acl_plan'])
    refs = [(name, p[name]) for name in ('migration_plan', 'validation', 'acl_source', 'acl_plan')]
    refs.append(('acl_policy', acl_plan['policy_ref']))
    private_refs = [p['acl_plan'], acl_plan['policy_ref']]
    if is_asr_recovery(p) or is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
        refs.append(('acl_new_objects', acl_plan['new_objects_ref']))
        private_refs.append(acl_plan['new_objects_ref'])
    if is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
        refs.append(('acl_restore_evidence', acl_plan['restore_evidence_ref']))
        private_refs.append(acl_plan['restore_evidence_ref'])
    if is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
        refs.append(('acl_source_objects', acl_plan['source_objects_ref']))
        private_refs.append(acl_plan['source_objects_ref'])
        validation = document(p['validation'])
        for name in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'):
            refs.append((name, validation[name]))
            private_refs.append(validation[name])
    refs.extend(('acl_source_' + str(index), {'path': name, 'sha256': digest})
                for index, (name, digest) in enumerate(sorted(acl_plan['source_files'].items())))
    refs.append(('migrator', file_ref(release/'database/scripts/migrate.py')))
    refs.extend(('migration_' + row['key'], {
        'path': str(release/'database'/row['file']), 'sha256': row['sha256']})
        for row in document(p['migration_plan']))
    for bound in private_refs:
        path = Path(bound['path']); info = path.stat()
        require(info.st_uid == account.pw_uid and stat.S_IMODE(info.st_mode) == 0o600,
                'Stage ACL plan and policy must be postgres-owned 0600')
    ancestors = set(owned.parents) | {owned}
    for _, bound in refs:
        read_ref(bound)
        ancestors.update(Path(bound['path']).parents)
    for path in sorted(ancestors):
        host.run(['runuser', '-u', 'postgres', '--', 'test', '-x', path])
    # Root-readable files are not necessarily readable by the database child.
    # Emit only stable labels/error types; never subprocess output or file contents.
    probe = """import hashlib,json,pathlib,sys
refs=json.loads(sys.argv[1])
for label,ref in refs:
    try:
        path=pathlib.Path(ref['path'])
        if path.is_symlink() or not path.is_file():
            raise OSError('regular file required')
        value=hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        print(json.dumps({'status':'unreadable','label':label,'error_type':type(exc).__name__}))
        break
    if value!=ref['sha256']:
        print(json.dumps({'status':'changed','label':label}))
        break
else:
    print(json.dumps({'status':'verified','count':len(refs)}))
"""
    observed = json.loads(host.run(['runuser', '-u', 'postgres', '--',
        release/'.venv/bin/python', '-c', probe, json.dumps(refs)]))
    labels = {label for label, _ in refs}
    label = observed.get('label') if observed.get('label') in labels else 'unknown'
    require(observed == {'status': 'verified', 'count': len(refs)},
            'Database peer input unreadable or changed: ' + label)


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
    path = folder/'DATABASE.json'
    process = {'returncode': returncode, 'database_receipt_present': path.is_file() and not path.is_symlink()}
    original.atomic_json(folder/'PEER_PROCESS.json', process)
    require(process['database_receipt_present'],
            f'Database peer exited with code {returncode} without a DATABASE.json receipt; inspect PEER_PROCESS.json')
    try:
        receipt = json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise Refused('Database peer receipt unreadable or malformed; inspect PEER_PROCESS.json') from None
    require(isinstance(receipt, dict), 'Database peer receipt must be an object; inspect PEER_PROCESS.json')
    return {'receipt': file_ref(path), 'returncode': returncode, 'result': receipt}


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


def verify_ledger_rows(p, plan, before, after):
    require(not after['running_jobs'] and verify_release_ledger(p, plan, after, final=True) == [],
            'Final ledger mismatch')
    after_rows = {r['migration_key']: r for r in after['checksum_rows']}
    require(all(after_rows.get(r['migration_key']) == r for r in before['checksum_rows']),
            'Old ledger bytes changed')
    registered_rows = {r['version']: r for r in after['registered_rows']}
    require(all(registered_rows.get(r['version']) == r for r in before['registered_rows']),
            'Old schema registration rows changed')
    require(all(after_rows[k]['provenance'] == 'executed' for k in p['pending']),
            'New migration provenance differs')


async def apply_fde_history(conn, p, plan, before, migrator, item, root, acl, result, save):
    """One commit for the reviewed V280 DDL, ledger and API-only function grant."""
    require(p['pending'] == ['V280'] and item.key == 'V280' and not conn.is_in_transaction(),
            'Exact V280 atomic migration required')
    attempt = {'key': 'V280', 'sha256': item.checksum, 'commit_outcome': 'not_attempted'}
    result['migrations'].append(attempt)
    result.update(phase='v280_atomic_begin', acl_commit_outcome='not_attempted')
    save()
    tx = conn.transaction(isolation='repeatable_read')
    await tx.start()
    try:
        await conn.execute("SET LOCAL lock_timeout='2s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        require(await ledger(conn) == before, 'Ledger changed before atomic migration')
        result['acl_before'] = await acl.verify_state(conn, plan_ref=p['acl_plan'])
        result['phase'] = 'migration_V280'; save()
        state = await migrator.apply(conn, item, root)  # nested transaction is a savepoint
        require(state == 'applied' and conn.is_in_transaction(), 'V280 escaped atomic transaction')
        result['phase'] = 'v280_api_execute_grant'; save()
        result['acl_result'] = await acl.grant_execute(conn, plan_ref=p['acl_plan'])
        after = await ledger(conn)
        verify_ledger_rows(p, plan, before, after)
        result['precommit_after'] = after
        result.update(phase='v280_commit', acl_commit_outcome='unknown')
        attempt.update(status=state, commit_outcome='unknown')
        save()  # durable evidence before the only COMMIT
        await tx.commit()
        attempt['commit_outcome'] = 'acknowledged'
        result['acl_commit_outcome'] = 'acknowledged'
        result['phase'] = 'v280_committed'; save()
    except BaseException:
        if conn.is_in_transaction():
            try:
                await tx.rollback()
                result['atomic_rollback_acknowledged'] = True
            except BaseException as cleanup:
                result['errors'].append({'cleanup': 'v280_rollback', 'type': type(cleanup).__name__})
        save()
        raise
    result['acl_verify'] = await acl.verify_release_permissions(conn, plan_ref=p['acl_plan'])


async def apply_follow_up_recency(conn, p, plan, before, migrator, item, root, acl, result, save):
    """One commit for the reviewed V281 DDL, ledger and API-only function grants."""
    require(p['pending'] == ['V281'] and item.key == 'V281' and not conn.is_in_transaction(),
            'Exact V281 atomic migration required')
    attempt = {'key': 'V281', 'sha256': item.checksum, 'commit_outcome': 'not_attempted'}
    result['migrations'].append(attempt)
    result.update(phase='v281_atomic_begin', acl_commit_outcome='not_attempted')
    save()
    tx = conn.transaction(isolation='repeatable_read')
    await tx.start()
    try:
        await conn.execute("SET LOCAL lock_timeout='2s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        require(await ledger(conn) == before, 'Ledger changed before atomic migration')
        result['acl_before'] = await acl.verify_state(conn, plan_ref=p['acl_plan'])
        result['phase'] = 'migration_V281'; save()
        state = await migrator.apply(conn, item, root)  # nested transaction is a savepoint
        require(state == 'applied' and conn.is_in_transaction(), 'V281 escaped atomic transaction')
        result['phase'] = 'v281_api_execute_grant'; save()
        result['acl_result'] = await acl.grant_execute(conn, plan_ref=p['acl_plan'])
        after = await ledger(conn)
        verify_ledger_rows(p, plan, before, after)
        result['precommit_after'] = after
        result.update(phase='v281_commit', acl_commit_outcome='unknown')
        attempt.update(status=state, commit_outcome='unknown')
        save()  # durable evidence before the only COMMIT
        await tx.commit()
        attempt['commit_outcome'] = 'acknowledged'
        result['acl_commit_outcome'] = 'acknowledged'
        result['phase'] = 'v281_committed'; save()
    except BaseException:
        if conn.is_in_transaction():
            try:
                await tx.rollback()
                result['atomic_rollback_acknowledged'] = True
            except BaseException as cleanup:
                result['errors'].append({'cleanup': 'v281_rollback', 'type': type(cleanup).__name__})
        save()
        raise
    result['acl_verify'] = await acl.verify_release_permissions(conn, plan_ref=p['acl_plan'])


async def apply_asr_recovery(conn, p, plan, before, migrator, items, root, acl, result, save):
    """One commit for all three ASR migrations, ledger rows and narrow grants."""
    require(p['pending'] == ['V282', 'V283', 'V284']
            and [item.key for item in items] == p['pending'] and not conn.is_in_transaction(),
            'Exact V282-V284 atomic migration required')
    attempts = [{'key': item.key, 'sha256': item.checksum, 'commit_outcome': 'not_attempted'} for item in items]
    result['migrations'].extend(attempts)
    result.update(phase='v284_atomic_begin', acl_commit_outcome='not_attempted'); save()
    tx = conn.transaction(isolation='repeatable_read')
    await tx.start()
    try:
        await conn.execute("SET LOCAL lock_timeout='2s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        require(await ledger(conn) == before, 'Ledger changed before atomic ASR migration')
        result['acl_before'] = await acl.verify_state(conn, plan_ref=p['acl_plan'])
        for item, attempt in zip(items, attempts):
            result['phase'] = 'migration_'+item.key; save()
            state = await migrator.apply(conn, item, root)  # savepoint only, never outer COMMIT
            require(state == 'applied' and conn.is_in_transaction(), 'ASR migration escaped atomic transaction')
            attempt['status'] = state
        result['phase'] = 'v284_additive_runtime_grants'; save()
        result['acl_result'] = await acl.grant_execute(conn, plan_ref=p['acl_plan'])
        after = await ledger(conn)
        verify_ledger_rows(p, plan, before, after)
        result['precommit_after'] = after
        result.update(phase='v284_commit', acl_commit_outcome='unknown')
        for attempt in attempts: attempt['commit_outcome'] = 'unknown'
        save()  # durable acknowledgement boundary for the only COMMIT
        await tx.commit()
        for attempt in attempts: attempt['commit_outcome'] = 'acknowledged'
        result.update(acl_commit_outcome='acknowledged', phase='v284_committed'); save()
    except BaseException:
        if conn.is_in_transaction():
            try:
                await tx.rollback()
                result['atomic_rollback_acknowledged'] = True
            except BaseException as cleanup:
                result['errors'].append({'cleanup': 'v284_rollback', 'type': type(cleanup).__name__})
        save()
        raise
    result['acl_verify'] = await acl.verify_release_permissions(conn, plan_ref=p['acl_plan'])


async def apply_task_execution(conn, p, plan, before, migrator, items, root, acl, result, save):
    """One commit for a scope-fixed migration batch, ledger and narrow grants."""
    expected_pending = ['V287', 'V288'] if is_task_lightweight(p) else ['V285', 'V286']
    phase = 'v288' if is_task_lightweight(p) else 'v286'
    if is_business_fixes(p):
        expected_pending, phase = ['V290', 'V291', 'V292'], 'v292'
    if is_customer_followup(p):
        expected_pending, phase = ['V293'], 'v293'
    if is_dashboard_targets(p):
        expected_pending, phase = ['V294','V295','V296'], 'v296'
    if is_opportunity_claim(p):
        expected_pending, phase = ['V297'], 'v297'
    if is_battlemap_report(p):
        expected_pending, phase = ['V298'], 'v298'
    require(p['pending'] == expected_pending
            and [item.key for item in items] == p['pending'] and not conn.is_in_transaction(),
            'Exact reviewed task atomic migration required')
    attempts = [{'key': item.key, 'sha256': item.checksum, 'commit_outcome': 'not_attempted'} for item in items]
    result['migrations'].extend(attempts)
    result.update(phase=phase + '_atomic_begin', acl_commit_outcome='not_attempted'); save()
    tx = conn.transaction(isolation='repeatable_read')
    await tx.start()
    try:
        await conn.execute("SET LOCAL lock_timeout='2s'")
        await conn.execute("SET LOCAL statement_timeout='30s'")
        require(await ledger(conn) == before, 'Ledger changed before atomic task migration')
        result['acl_before'] = await acl.verify_state(conn, plan_ref=p['acl_plan'])
        result['business_before'] = await acl.business_state(conn)
        for item, attempt in zip(items, attempts):
            result['phase'] = 'migration_'+item.key; save()
            state = await migrator.apply(conn, item, root)  # savepoint only, never outer COMMIT
            require(state == 'applied' and conn.is_in_transaction(), 'task migration escaped atomic transaction')
            attempt['status'] = state
        result['phase'] = phase + ('_verify_preserved_grants' if is_battlemap_report(p) else '_additive_runtime_grants'); save()
        result['acl_result'] = await acl.grant_execute(conn, plan_ref=p['acl_plan'])
        result['business_after'] = await acl.business_state(conn)
        require(result['business_after'] == result['business_before'], 'Task migration changed pre-existing business rows')
        after = await ledger(conn)
        verify_ledger_rows(p, plan, before, after)
        result['precommit_after'] = after
        result.update(phase=phase + '_commit', acl_commit_outcome='unknown')
        for attempt in attempts: attempt['commit_outcome'] = 'unknown'
        save()  # durable acknowledgement boundary for the only COMMIT
        await tx.commit()
        for attempt in attempts: attempt['commit_outcome'] = 'acknowledged'
        result.update(acl_commit_outcome='acknowledged', phase=phase + '_committed'); save()
    except BaseException:
        if conn.is_in_transaction():
            try:
                await tx.rollback()
                result['atomic_rollback_acknowledged'] = True
            except BaseException as cleanup:
                result['errors'].append({'cleanup': phase + '_rollback', 'type': type(cleanup).__name__})
        save()
        raise
    result['acl_verify'] = await acl.verify_release_permissions(conn, plan_ref=p['acl_plan'])


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
        acl, acl_plan = validated_acl(p)
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
        if is_visit_index(p):
            result['phase'] = 'security_verifying_before'; save()
            result['acl_before'] = await acl.verify_preserved_permissions(conn, plan_ref=p['acl_plan'])
        save()
        if is_fde_history(p):
            await apply_fde_history(conn, p, plan, before, migrator, by_key['V280'],
                                    release/'database', acl, result, save)
        if is_follow_up_recency(p):
            await apply_follow_up_recency(conn, p, plan, before, migrator, by_key['V281'],
                                          release/'database', acl, result, save)
        if is_asr_recovery(p):
            await apply_asr_recovery(conn, p, plan, before, migrator, [by_key[key] for key in p['pending']],
                                     release/'database', acl, result, save)
        if is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
            await apply_task_execution(conn, p, plan, before, migrator, [by_key[key] for key in p['pending']],
                                       release/'database', acl, result, save)
        for key in ([] if is_fde_history(p) or is_follow_up_recency(p) or is_asr_recovery(p) or is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p) else p['pending']):
            attempt = {'key': key, 'sha256': by_key[key].checksum, 'commit_outcome': 'unknown'}
            result['phase'] = 'migration_'+key
            result['migrations'].append(attempt); save()  # must precede apply/COMMIT
            require(not conn.is_in_transaction(), 'Per-file commits require no outer transaction')
            state = await migrator.apply(conn, by_key[key], release/'database')
            require(state == 'applied', 'Pending migration was not freshly applied')
            attempt.update(status=state, commit_outcome='acknowledged'); save()
        if is_fde_history(p) or is_follow_up_recency(p) or is_asr_recovery(p) or is_task_execution(p) or is_business_fixes(p) or is_customer_followup(p) or is_dashboard_targets(p) or is_opportunity_claim(p) or is_battlemap_report(p):
            pass  # DDL, ledger and grant were verified before the single atomic commit.
        elif is_visit_index(p):
            result['phase'] = 'security_verifying_after'
            result['acl_commit_outcome'] = 'not_attempted'
            result['acl_result'] = {'mode': 'preserve', 'acl_writes': False}; save()
            result['acl_verify'] = await acl.verify_preserved_permissions(conn, plan_ref=p['acl_plan'], final=True)
        else:
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
