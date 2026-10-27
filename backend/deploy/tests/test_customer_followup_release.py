"""Offline admission checks; these are not production migration evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend/deploy'))
import release_customer_followup_contract as contract
import release_customer_followup_security as security
import release_forward_bundle as forward
from release_bundle_control import Refused, digest


def fixture():
    frozen = json.loads((ROOT / 'backend/tests/fixtures/release122_knowledge_contract.frozen.json').read_text())
    rows = deepcopy(frozen['migration_plan'])
    for version in range(276, 293):
        if version == 289:
            continue
        path = next((ROOT / 'database/migrations').glob(f'V{version}__*.sql'))
        rows.append({'key': f'V{version}', 'file': 'migrations/' + path.name,
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    prior = {row['key']: row['sha256'] for row in rows}
    registered = sorted(frozen['expected_ledger']['registered']
                        + [f'V{v}' for v in range(269, 293) if v != 289])
    name, checksum = contract.SQL['V293']
    rows.append({'key': 'V293', 'file': name, 'sha256': checksum})
    plan = {
        'product_version': '1.2.10', 'release_scope': contract.RELEASE_SCOPE.copy(),
        'source_schema': 'V292', 'target_schema': 'V293', 'pending': ['V293'],
        'expected_ledger': {'checksums': prior, 'registered': registered, 'running_jobs': 0},
        'expected_database': {'name': 'sales_saas', 'schema': 'V292',
                              'ledger_sha256': contract.BASE_COMPLETE_LEDGER},
        'candidate': {'backend': {'revision': 'a' * 40, 'sha256': 'b' * 64}},
        'active_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
        'supported_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
    }
    return plan, rows


class CustomerFollowupRelease(unittest.TestCase):
    def test_exact_ledger_and_preserved_clients(self):
        plan, rows = fixture()
        self.assertIs(forward.knowledge_contract_for(plan), contract)
        self.assertFalse(forward.is_business_fixes(plan))
        contract.validate(plan, rows, digest=digest, require=forward.require)
        self.assertEqual(forward.verify_release_ledger(plan, rows, plan['expected_ledger']), ['V293'])
        contract.validate_migrations(rows, {r['key']: r['sha256'] for r in rows},
            sorted(plan['expected_ledger']['registered'] + ['V293']), 'V293', 'V293', [],
            digest=digest, require=forward.require)
        for fault in ('checksum', 'registration', 'mini', 'scope', 'jobs'):
            with self.subTest(fault=fault):
                bad = deepcopy(plan)
                if fault == 'checksum': bad['expected_ledger']['checksums']['BASELINE_V1'] = '0' * 64
                if fault == 'registration': bad['expected_ledger']['registered'].pop()
                if fault == 'mini': bad['supported_mini_versions'].append('1.2.10+mini.' + 'a' * 40)
                if fault == 'scope': bad['release_scope'] = ['other']
                if fault == 'jobs': bad['expected_ledger']['running_jobs'] = 1
                with self.assertRaises(Refused):
                    contract.validate(bad, rows, digest=digest, require=forward.require)

    def test_exact_new_helper_grants(self):
        objects = {'format': 'salesbuddy-customer-followup-new-objects-v1', 'functions': {}}
        for signature in (*security.FUNCTION_GRANTS, *security.OLD_FUNCTIONS):
            roles = security.FUNCTION_GRANTS.get(signature, [])
            objects['functions'][signature] = {
                'properties': {'owner': 'postgres', 'definer': True, 'settings': ['search_path=pg_catalog'],
                               'language': 'sql', 'volatility': 's', 'kind': 'f'},
                'acl': [{'grantor': 'postgres', 'grantee': security.ROLES[role],
                         'privilege': 'EXECUTE', 'grantable': False} for role in roles],
            }
        security.validate_new_acl(objects)
        private = 'security.customer_claim_visit_customer_access(uuid)'
        for grantee in ('PUBLIC', 'sales_api_saas', 'sales_worker_saas', 'sales_platform_saas'):
            with self.subTest(grantee=grantee):
                bad = deepcopy(objects)
                bad['functions'][private]['acl'].append({'grantor': 'postgres', 'grantee': grantee,
                                                        'privilege': 'EXECUTE', 'grantable': False})
                with self.assertRaises(Refused):
                    security.validate_new_acl(bad, allow_ungranted=True)
        self.assertEqual(len(security.GRANTS), 3)
        self.assertFalse(hasattr(security, 'apply_release_permissions'))

    def test_admission_does_not_reuse_ci_exception_or_distribute_mini(self):
        plan, _ = fixture()
        backend, web = plan['candidate']['backend'], {'revision': 'a' * 40, 'sha256': 'c' * 64}
        evidence = {
            'format': contract.VALIDATION_FORMAT, 'release_scope': contract.RELEASE_SCOPE.copy(),
            'status': 'reviewed_with_targeted_checks', 'targeted_checks': 'passed',
            'human_acceptance': 'pending', 'cloud_ci': 'passed',
            'release_authorization': 'current_release_explicit_user_authorization',
            'reviewed_backend': backend, 'reviewed_web': web, 'static_syntax': 'verified',
            'web_build': 'completed', 'mini_distribution': 'unchanged',
            'client_compatibility_method': 'static_contract_review',
            'reviewed_mini_versions': contract.ACTIVE_MINI_VERSIONS.copy(),
            **{key: {'path': '/private/fixture/' + key, 'sha256': 'e' * 64}
               for key in ('source_review_ref', 'build_receipt_ref', 'authorization_ref')},
        }
        def check(value):
            contract.validate_admission(value, product='1.2.10', backend=backend, web=web,
                                        clients=contract.ACTIVE_MINI_VERSIONS, require=forward.require)
        check(evidence)
        blocked = {**evidence, 'cloud_ci': 'not_run_billing_blocked'}
        with self.assertRaises(Refused): check(blocked)
        check({**blocked, 'ci_exception_authorization': 'current_release_explicit_user_authorization'})
        with self.assertRaises(Refused): check({**evidence, 'mini_distribution': 'uploaded'})


if __name__ == '__main__':
    unittest.main()
