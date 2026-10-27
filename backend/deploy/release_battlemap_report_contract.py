"""Exact V297 -> V298 private score policies and paired report/task fixes.

Preserve all distributed clients and original catalog/data. Application rollback
on V298 retains the scoring policies; schema reversal needs separate authorization.
"""

RELEASE_SCOPE = ['release_1.2.14_battlemap_report']
PENDING = ['V298']
BASE_LEDGER = '9a6c6d824f8c10385f68c58e0defe630bc1a80e8995b73bdf0b4d8295e46c53e'
BASE_REGISTERED = 'ec3ba16f4cd460a89528d79ec1743b626850957f31aae5ddabdf36c05ece5b6b'
BASE_COMPLETE_LEDGER = '91af6d799a2ad9d6229ae12403d019aa5ca5f37ff2208abbc95120ea3025fac5'
TARGET_LEDGER = '78a3b92ae6b998754bd47d68f0decbf0c495619fd57cf7c80ca03be14c9304b5'
TARGET_REGISTERED = '41604dd36898eb97a01f6e291e10062f75a8c30fefbb24678dbfb6aa29ee5dde'
TARGET_COMPLETE_LEDGER = 'ca4898b82b1df34aa27f512e4038c41d520d545b4a7787d3b5044988e5ee1301'
MIGRATION_PLAN_SHA = 'ef8a46905d49ea7df7eaafc75053c2b6f8073a8da13052f5619199bde3e848d1'
INSTALLATION_POLICY_SHA = 'c5fad510d4bf6e3fa692df28b2a1dbdd2c11a2373a2d7df61f09d7349e9d2bbc'
SQL = {'V298': ('migrations/V298__battle_map_self_score_write.sql', 'c3796a2a7d0ffbd4e02b2c1dc338865222b5ad606257fdad8ec20887be671df0')}
ACTIVE_MINI_VERSIONS = ['1.1.1', '1.1.2', '1.2.0', '1.2.1', '1.2.1+mini.084199244e3ab09cdf3e057a7751220e5f0b2ae7', '1.2.1+mini.89774df68d775dbf24055ae9aea5e8c69ec4ae62', '1.2.1+mini.a9b938ce25a11a3fedfac612a9b6682f9384337f', '1.2.1+mini.e1203ab110478d0edaea37aa29d484ef9ebd7451', '1.2.10', '1.2.10+mini.0ca0528a2746e7da988c13a44e6d6e441c43b7ce', '1.2.10+mini.4c43dbe8f392067f5c2fc5828ed97640d22348f4', '1.2.10+mini.4fb089972864d0b23a26b15b19b4766ab571237e', '1.2.10+mini.9ae0f7cb3cde10c7683bc0a32cc4a8b9cef232b3', '1.2.11', '1.2.11+mini.9462f74341afded74e07f1eb919d38925f12adef', '1.2.12', '1.2.12+mini.4c7e2288b2bc681ea26475bbcab6028f5ba9783e', '1.2.13', '1.2.13+mini.553b5336b021001efde4041f5ab1ec9f8f9110bd', '1.2.2', '1.2.2+mini.11964a8633739e8d36d21544c56b2e5832219d36', '1.2.2+mini.253724c80f2470a5b424c7e8397b65e7adf75b36', '1.2.2+mini.2917c1cec34688eb6998b91a5d57d15c3a5ad159', '1.2.3', '1.2.3+mini.b5ffbdc03dd6bf877531bde488d0b55fab8d0d99', '1.2.4', '1.2.4+mini.b9a32ef20bc76d15d1436440f442beff3e259360', '1.2.5', '1.2.5+mini.23a4d67fac911b10946620a3d87c888d415c56e9', '1.2.6', '1.2.6+mini.1548fe2b43c90a31121c01391e9c25cc8a075816', '1.2.7', '1.2.8', '1.2.8+mini.bc20474bbb0bae2018af65d0e97145d32c062d21', '1.2.9', '1.2.9+mini.bf21638a33dacf653be87fba689824525a41de14']

GATE_FILES = (
    'release_battlemap_report_contract.py', 'release_battlemap_report_security.py',
    'release_forward_bundle.py', 'release_upgrade.py', 'release_bundle_control.py',
    'release_visit_index_security.py', 'release122_visit_index_contract.py',
)
SECURITY_SECTIONS = {
    'relations', 'columns', 'functions', 'schemas', 'roles', 'memberships',
    'defaults', 'policies', 'database', 'database_role_settings',
    'modified_function_properties', 'constraints', 'triggers', 'column_definitions', 'view_definitions',
}
VALIDATION_FORMAT = 'salesbuddy-battlemap-report-admission-v1'


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row['key']: row['sha256'] for row in migrations}
    require(target == 'V298' and len(migrations) == len(after) == 256
            and digest(migrations) == MIGRATION_PLAN_SHA and digest(after) == TARGET_LEDGER
            and 'V289' not in after and all(next((r for r in migrations if r['key'] == k), None)
                == {'key': k, 'file': v[0], 'sha256': v[1]} for k, v in SQL.items()),
            'Release 1.2.14 requires exactly the frozen V298 migration plan without V289')
    registered = list(registered)
    require(len(registered) == len(set(registered)), 'Duplicate migration registrations')
    complete = {'registered': sorted(registered), 'checksums': prior}
    if pending:
        require(source == 'V297' and pending == PENDING and len(prior) == 255
                and digest(prior) == BASE_LEDGER and len(registered) == 286
                and digest(sorted(registered)) == BASE_REGISTERED and digest(complete) == BASE_COMPLETE_LEDGER
                and set(after) == set(prior) | set(PENDING) and all(after[k] == v for k, v in prior.items()),
                'Only V298 may extend the unchanged complete production V297 ledger')
    else:
        require(source == 'V298' and prior == after and len(registered) == 287
                and digest(sorted(registered)) == TARGET_REGISTERED
                and digest(complete) == TARGET_COMPLETE_LEDGER, 'Final V298 complete ledger differs')


def validate(plan, migrations, *, digest, require):
    require(plan.get('product_version') == '1.2.14' and plan.get('release_scope') == RELEASE_SCOPE
            and plan.get('pending') == PENDING and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None
            and plan['expected_ledger'].get('running_jobs') == 0
            and plan.get('expected_database') == {'name': 'sales_saas', 'schema': 'V297', 'ledger_sha256': BASE_COMPLETE_LEDGER},
            'Exact idle V297 production and unchanged configuration required')
    revision = plan.get('candidate', {}).get('backend', {}).get('revision')
    require(_hex(revision, 40), 'A fixed backend release commit is required')
    require(plan.get('active_mini_versions') == ACTIVE_MINI_VERSIONS
            and plan.get('supported_mini_versions') == ACTIVE_MINI_VERSIONS,
            'Keep all 36 distributed clients; register the new exact Mini only after backend verification')
    validate_migrations(migrations, plan['expected_ledger']['checksums'], plan['expected_ledger']['registered'],
                        plan['source_schema'], plan['target_schema'], plan['pending'], digest=digest, require=require)


def validate_admission(evidence, *, product, backend, web, clients, require):
    """Record scoped checks honestly, without claiming full end-to-end acceptance."""
    require(isinstance(evidence, dict) and evidence.get('format') == VALIDATION_FORMAT
            and product == '1.2.14' and evidence.get('release_scope') == RELEASE_SCOPE
            and evidence.get('status') == 'reviewed_with_targeted_checks'
            and evidence.get('targeted_checks') == 'passed'
            and evidence.get('human_acceptance') == 'pending'
            and evidence.get('release_authorization') == 'current_release_explicit_user_authorization'
            and evidence.get('reviewed_backend') == backend and evidence.get('reviewed_web') == web
            and _hex(backend.get('revision'), 40)
            and evidence.get('static_syntax') == 'verified' and evidence.get('web_build') == 'completed'
            and evidence.get('mini_distribution') == 'unchanged'
            and evidence.get('client_compatibility_method') == 'static_contract_review'
            and evidence.get('reviewed_mini_versions') == clients == ACTIVE_MINI_VERSIONS
            and not any(key.startswith('tested_') for key in evidence),
            'Exact paired source, targeted checks, Web build and unchanged Mini distribution required')
    # SSH release is explicitly authorized. Record CI truthfully without requiring a PR.
    require(evidence.get('cloud_ci') in ('passed','not_run','not_run_billing_blocked','unavailable'),
            'Record the actual cloud CI state; do not claim checks which did not run')
    for key in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'):
        ref = evidence.get(key, {})
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and _hex(ref.get('sha256'), 64),
                'Admission requires immutable source/checks/build/current-authorization receipts')


def validate_acl(plan, snapshot, *, digest, require):
    versions = plan.get('expected_registered_versions')
    require(plan.get('format') == 'salesbuddy-battlemap-report-security-plan-v1'
            and plan.get('release_scope') == RELEASE_SCOPE and plan.get('mode') == 'battlemap_self_score_policies'
            and plan.get('maintenance_owner') == 'postgres'
            and plan.get('roles') == {'api': 'sales_api_saas', 'worker': 'sales_worker_saas',
                                     'feishu': 'sales_feishu_saas', 'platform': 'sales_platform_saas'}
            and plan.get('target') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(versions, list) and len(versions) == 287 and versions == sorted(set(versions))
            and digest(versions) == TARGET_REGISTERED, 'Exact V298 security plan required')
    fingerprints = snapshot.get('fingerprints', {})
    require(snapshot.get('format') == 'salesbuddy-live-security-v1' and set(fingerprints) == SECURITY_SECTIONS
            and all(_hex(v, 64) for v in fingerprints.values()) and _hex(snapshot.get('index_fingerprint'), 64)
            and snapshot.get('runtime_roles_nonprivileged') is True, 'Complete preserved production security required')


def validate_restore_evidence(evidence, objects, source_objects, *, digest, require):
    restored = evidence.get('restored_database', {})
    require(evidence.get('format') == 'salesbuddy-battlemap-report-restore-rehearsal-v1'
            and evidence.get('status') == 'passed'
            and evidence.get('evidence_kind') == 'independent_production_restore'
            and evidence.get('source_database') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(restored, dict) and set(restored) == {'database', 'oid'}
            and isinstance(restored['database'], str) and restored['database'] != 'sales_saas'
            and isinstance(restored['oid'], int) and restored['oid'] != 18684
            and _hex(evidence.get('source_backup_sha256'), 64)
            and evidence.get('restored_from_production_backup') is True
            and evidence.get('source_schema') == 'V297' and evidence.get('target_schema') == 'V298'
            and evidence.get('source_ledger_sha256') == BASE_COMPLETE_LEDGER
            and evidence.get('target_ledger_sha256') == TARGET_COMPLETE_LEDGER
            and evidence.get('migration_plan_sha256') == MIGRATION_PLAN_SHA
            and evidence.get('new_objects_sha256') == digest(objects)
            and evidence.get('source_objects_sha256') == digest(source_objects)
            and evidence.get('security_preserved') is True and evidence.get('business_state_preserved') is True
            and evidence.get('effective_privileges_verified') is True,
            'A fresh matching production-backup restore and exact security/object evidence are required')


def validate_admission_receipts(evidence, receipts, *, require):
    revision = evidence['reviewed_backend']['revision']
    source, build, authorization = (receipts[k] for k in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'))
    require(source.get('format') == 'salesbuddy-battlemap-report-source-review-v1'
            and source.get('revision') == revision and source.get('static_syntax') == 'verified'
            and source.get('targeted_checks') == 'passed'
            and isinstance(source.get('check_commands'), list) and bool(source['check_commands'])
            and all(isinstance(c, str) and c.strip() for c in source['check_commands'])
            and _hex(source.get('check_receipt_sha256'), 64)
            and source.get('client_compatibility_method') == 'static_contract_review'
            and source.get('reviewed_mini_versions') == evidence['reviewed_mini_versions'],
            'Source receipt must identify final source, scoped checks and all preserved clients')
    require(build.get('format') == 'salesbuddy-battlemap-report-build-receipt-v1'
            and build.get('revision') == revision and build.get('backend') == evidence['reviewed_backend']
            and build.get('web') == evidence['reviewed_web']
            and build.get('web_build') == 'completed' and build.get('mini_distribution') == 'unchanged',
            'Build receipt must identify paired artifacts and unchanged Mini distribution')
    require(authorization.get('format') == 'salesbuddy-battlemap-report-human-authorization-v1'
            and authorization.get('release_scope') == RELEASE_SCOPE and authorization.get('revision') == revision
            and authorization.get('release_authorization') == evidence['release_authorization']
            and isinstance(authorization.get('release_authorization_message'), str)
            and bool(authorization['release_authorization_message'].strip())
            and authorization.get('cloud_ci') == evidence['cloud_ci'],
            'This release requires its own explicit human release decision and actual CI state')
