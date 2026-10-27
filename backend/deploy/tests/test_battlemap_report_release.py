"""Focused offline release checks, never a substitute for the production restore."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend/deploy'))
import release_battlemap_report_contract as contract
import release_battlemap_report_security as security
import release_forward_bundle as forward
from release_bundle_control import Refused, digest


def fixture():
    frozen = json.loads((ROOT / 'backend/tests/fixtures/release122_knowledge_contract.frozen.json').read_text())
    rows = deepcopy(frozen['migration_plan'])
    for version in range(276, 299):
        if version == 289:
            continue
        path = next((ROOT / 'database/migrations').glob(f'V{version}__*.sql'))
        rows.append({'key': f'V{version}', 'file': 'migrations/' + path.name,
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    prior = {r['key']: r['sha256'] for r in rows if r['key'] not in contract.PENDING}
    registered = sorted(frozen['expected_ledger']['registered']
                        + [f'V{v}' for v in range(269, 298) if v != 289])
    return {
        'product_version': '1.2.14', 'release_scope': contract.RELEASE_SCOPE.copy(),
        'source_schema': 'V297', 'target_schema': 'V298', 'pending': contract.PENDING.copy(),
        'expected_ledger': {'checksums': prior, 'registered': registered, 'running_jobs': 0},
        'expected_database': {'name': 'sales_saas', 'schema': 'V297',
                              'ledger_sha256': contract.BASE_COMPLETE_LEDGER},
        'candidate': {'backend': {'revision': 'a' * 40, 'sha256': 'b' * 64}},
        'active_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
        'supported_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
    }, rows


def objects_fixture():
    return {'format':'salesbuddy-battlemap-report-new-objects-v1','policies':[
        {'schemaname':'insight','tablename':'quadrant_score','policyname':name,
         'permissive':'PERMISSIVE','roles':['public'],'cmd':command,
         'qual':None if command=='INSERT' else security.POLICY_PREDICATE,
         'with_check':security.POLICY_PREDICATE}
        for name,command in zip(security.NEW_POLICIES,('INSERT','UPDATE'))]}


class BattlemapReportRelease(unittest.TestCase):
    def test_only_two_exact_self_score_policies_no_grants(self):
        objects=objects_fixture();security.validate_new_acl(objects)
        self.assertEqual(security.GRANTS,())
        for field,value in [('schemaname','crm'),('tablename','customer'),('policyname','other'),
                            ('permissive','RESTRICTIVE'),('roles',['sales_worker_saas']),
                            ('cmd','ALL'),('qual','true'),('with_check','true')]:
            with self.subTest(field=field):
                bad=deepcopy(objects);bad['policies'][0][field]=value
                with self.assertRaises(Refused):security.validate_new_acl(bad,allow_ungranted=True)
        for policies in ([],objects['policies'][:1],objects['policies']*2):
            with self.assertRaises(Refused):security.validate_new_acl({**objects,'policies':policies})
        for relation in [('crm','quadrant_score'),('insight','other')]:
            row=deepcopy(objects['policies'][0]);row.update(schemaname=relation[0],tablename=relation[1])
            self.assertFalse(security.is_new_policy(row))

    def test_verify_hook_never_executes_grants(self):
        class Conn:
            def is_in_transaction(self):return True
            async def execute(self,*args):raise AssertionError('V298 verifier cannot execute SQL mutations')
        calls=[]
        async def verify(conn,**kwargs):calls.append(kwargs);return {'runtime_grants_changed':False}
        with patch.object(security,'verify_state',verify):
            self.assertEqual(asyncio.run(security.grant_execute(Conn(),plan_ref={'fixed':True})),
                             {'runtime_grants_changed':False})
        self.assertEqual(calls,[{'plan_ref':{'fixed':True},'final':True}])

    def test_business_scan_timeout_is_local_and_restores_ddl_budget(self):
        for original in ('30s', '1min'):
            for fail in (False, True):
                with self.subTest(original=original, fail=fail):
                    class Conn:
                        timeout = original
                        def is_in_transaction(self): return True
                        async def fetchval(self, sql):
                            self.assert_sql = sql
                            return self.timeout
                        async def execute(self, sql, *args):
                            if sql == "SET LOCAL statement_timeout='120s'": self.timeout = '120s'
                            else:
                                self.restoration = (sql, args)
                                self.timeout = args[0]
                    conn = Conn()
                    async def scan(connection):
                        self.assertEqual(connection.timeout, '120s')
                        if fail: raise RuntimeError('scan cancelled')
                        return {'unchanged_full_digest': True}
                    with patch.object(security, '_business_state_digest', scan):
                        if fail:
                            with self.assertRaisesRegex(RuntimeError, 'scan cancelled'):
                                asyncio.run(security.business_state(conn))
                        else:
                            self.assertEqual(asyncio.run(security.business_state(conn)), {'unchanged_full_digest': True})
                    self.assertEqual(conn.timeout, original)
                    self.assertEqual(conn.assert_sql, 'SHOW statement_timeout')
                    self.assertEqual(conn.restoration,
                        ("SELECT set_config('statement_timeout',$1,true)", (original,)))


    def test_exact_ledger_and_preserved_clients(self):
        plan, rows = fixture()
        self.assertIs(forward.knowledge_contract_for(plan), contract)
        self.assertFalse(forward.is_customer_followup(plan))
        contract.validate(plan, rows, digest=digest, require=forward.require)
        self.assertEqual(forward.verify_release_ledger(plan, rows, plan['expected_ledger']), contract.PENDING)
        contract.validate_migrations(rows, {r['key']: r['sha256'] for r in rows},
            sorted(plan['expected_ledger']['registered'] + contract.PENDING), 'V298', 'V298', [],
            digest=digest, require=forward.require)
        self.assertEqual(digest(json.loads((ROOT/'tools/installation/runtime_permissions.json').read_text())),
                         contract.INSTALLATION_POLICY_SHA)
        for fault in ('checksum', 'registration', 'mini', 'scope', 'jobs', 'partial', 'configuration'):
            with self.subTest(fault=fault):
                bad = deepcopy(plan)
                if fault == 'checksum': bad['expected_ledger']['checksums']['BASELINE_V1'] = '0' * 64
                if fault == 'registration': bad['expected_ledger']['registered'].pop()
                if fault == 'mini': bad['supported_mini_versions'].append('1.2.10+mini.' + 'a' * 40)
                if fault == 'scope': bad['release_scope'] = ['other']
                if fault == 'jobs': bad['expected_ledger']['running_jobs'] = 1
                if fault == 'partial': bad['pending'] = ['V297','V298']
                if fault == 'configuration': bad['prepared_configuration_delta'] = {'local': True}
                with self.assertRaises(Refused): contract.validate(bad, rows, digest=digest, require=forward.require)


    def test_admission_records_ci_without_imposing_pr_gate(self):
        plan, _ = fixture()
        backend, web = plan['candidate']['backend'], {'revision': 'a' * 40, 'sha256': 'c' * 64}
        evidence = {
            'format': contract.VALIDATION_FORMAT, 'release_scope': contract.RELEASE_SCOPE.copy(),
            'status': 'reviewed_with_targeted_checks', 'targeted_checks': 'passed',
            'human_acceptance': 'pending', 'cloud_ci': 'not_run',
            'release_authorization': 'current_release_explicit_user_authorization',
            'reviewed_backend': backend, 'reviewed_web': web, 'static_syntax': 'verified',
            'web_build': 'completed', 'mini_distribution': 'unchanged',
            'client_compatibility_method': 'static_contract_review',
            'reviewed_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
            **{k: {'path': '/private/fixture/' + k, 'sha256': 'e' * 64}
               for k in ('source_review_ref', 'build_receipt_ref', 'authorization_ref')},
        }
        def check(value):
            contract.validate_admission(value, product='1.2.14', backend=backend, web=web,
                                        clients=contract.ACTIVE_MINI_VERSIONS, require=forward.require)
        for ci in ('passed', 'not_run', 'not_run_billing_blocked', 'unavailable'):
            check({**evidence, 'cloud_ci': ci})
        for delta in ({'release_authorization': None}, {'mini_distribution': 'uploaded'},
                      {'human_acceptance': 'passed'}, {'tested_mini_versions': []}):
            with self.assertRaises(Refused): check({**evidence, **delta})


    def test_synthetic_receipt_cannot_replace_production_restore(self):
        objects, source = objects_fixture(), {'source': 'fixture'}
        evidence = {'format': 'salesbuddy-battlemap-report-restore-rehearsal-v1', 'status': 'passed',
            'evidence_kind': 'independent_production_restore', 'source_database': {'database': 'sales_saas', 'oid': 18684},
            'restored_database': {'database': 'independent_restore', 'oid': 20000},
            'source_backup_sha256': 'a' * 64, 'restored_from_production_backup': True,
            'source_schema': 'V297', 'target_schema': 'V298', 'source_ledger_sha256': contract.BASE_COMPLETE_LEDGER,
            'target_ledger_sha256': contract.TARGET_COMPLETE_LEDGER, 'migration_plan_sha256': contract.MIGRATION_PLAN_SHA,
            'new_objects_sha256': digest(objects), 'source_objects_sha256': digest(source),
            'security_preserved': True, 'business_state_preserved': True, 'effective_privileges_verified': True}
        contract.validate_restore_evidence(evidence, objects, source, digest=digest, require=forward.require)
        for delta in ({'evidence_kind': 'synthetic'}, {'restored_from_production_backup': False},
                      {'restored_database': evidence['source_database']}, {'new_objects_sha256': '0' * 64},
                      {'business_state_preserved': False}):
            with self.assertRaises(Refused):
                contract.validate_restore_evidence({**evidence, **delta}, objects, source, digest=digest, require=forward.require)


    def test_migration_and_security_verification_share_one_commit(self):
        for fail_at in (None, 'V298', 'grant', 'business', 'commit'):
            with self.subTest(fail_at=fail_at):
                events, saved, state = [], [], {'transaction': False}
                class Tx:
                    async def start(self): state['transaction'] = True; events.append('start')
                    async def rollback(self): state['transaction'] = False; events.append('rollback')
                    async def commit(self):
                        events.append('commit')
                        if fail_at == 'commit': raise ConnectionError('commit response lost')
                        state['transaction'] = False
                class Conn:
                    def is_in_transaction(self): return state['transaction']
                    def transaction(self, **kwargs): return Tx()
                    async def execute(self, sql): pass
                async def ledger(conn): return {'same': True}
                async def apply(conn, item, root):
                    self.assertTrue(conn.is_in_transaction()); events.append(item.key)
                    if fail_at == item.key: raise RuntimeError('migration failed')
                    return 'applied'
                async def verify(conn, **kwargs): self.assertTrue(conn.is_in_transaction()); return {'ok': True}
                async def grant(conn, **kwargs):
                    self.assertTrue(conn.is_in_transaction()); events.append('grant')
                    if fail_at == 'grant': raise RuntimeError('grant failed')
                    return {'ok': True}
                async def readonly(conn, **kwargs):
                    self.assertFalse(conn.is_in_transaction()); events.append('readonly'); return {'ok': True}
                async def business(conn):
                    return {'state': 'changed' if fail_at == 'business' and 'grant' in events else 'same'}
                result = {'migrations': [], 'errors': []}
                args = (Conn(), {'release_scope': contract.RELEASE_SCOPE, 'pending': contract.PENDING, 'acl_plan': {}},
                    [], {'same': True}, SimpleNamespace(apply=apply),
                    [SimpleNamespace(key=k, checksum=k) for k in contract.PENDING], ROOT,
                    SimpleNamespace(verify_state=verify, grant_execute=grant, verify_release_permissions=readonly,
                                    business_state=business), result, lambda: saved.append(deepcopy(result)))
                with patch.object(forward, 'ledger', ledger), patch.object(forward, 'verify_ledger_rows', lambda *a: None):
                    if fail_at:
                        with self.assertRaises((RuntimeError, ConnectionError, Refused)):
                            asyncio.run(forward.apply_task_execution(*args))
                        self.assertIn('rollback', events); self.assertNotIn('readonly', events)
                        self.assertTrue(all(r['commit_outcome'] != 'acknowledged' for r in result['migrations']))
                        self.assertEqual(events.count('commit'), int(fail_at == 'commit'))
                    else:
                        asyncio.run(forward.apply_task_execution(*args))
                        self.assertEqual(events, ['start', 'V298', 'grant', 'commit', 'readonly'])
                        self.assertTrue(any(s['phase'] == 'v298_commit' and all(r['commit_outcome'] == 'unknown'
                                            for r in s['migrations']) for s in saved))


if __name__=='__main__': unittest.main()
