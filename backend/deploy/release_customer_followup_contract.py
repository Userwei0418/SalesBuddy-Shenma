"""Exact V292 -> V293 customer follow-up release, preserving distributed clients.

The forward plan is additive to the exact live ledger. Reverting application code
on V293 does not revert V293 read/write semantics. A V292 schema/body restoration
is a separate reviewed operation; this adapter never rewrites migration history.
"""

RELEASE_SCOPE = ['release_1.2.10_customer_follow_up']
PENDING = ['V293']
BASE_LEDGER = '6854f9e95825d11a20095dbf26ef0bf5486f28f38f732e426a8ec89448dde352'
BASE_REGISTERED = '523e08511b97b3a1095dacbf85d5794046420f9a95b9488cd35138accb6ec79f'
BASE_COMPLETE_LEDGER = '9205a5f174475c384dd8a847333920e3b27dc7207efae28404255997e3022de1'
TARGET_LEDGER = '76707014636bc791cff95761d72347d2a85f83808b13ce83a1a638cb626c3419'
TARGET_REGISTERED = '72fa297164246a6c3b1a54bd466f20a891c54cb0a2badf78375634135f877993'
TARGET_COMPLETE_LEDGER = '9f55d067c2c5c62a930b52e71d2a49e70de7f2b4b4e512c2efed095f180b4b8f'
MIGRATION_PLAN_SHA = '7344ccc4d72ba51b8c026e8707b57ae8d317138d48785034d85b9b24115a1cf9'
INSTALLATION_POLICY_SHA = '337895090300ecf1e0e8f310bf0dd3da0cbffd94e98f7bb3f30b013ae2ec042a'
SQL = {'V293': ('migrations/V293__company_customer_follow_up.sql', 'c9e7758acd526a930d570c38fa730de010c2ec9aaaa3ae3c4ef20354b3049707')}
ACTIVE_MINI_VERSIONS = ['1.1.1', '1.1.2', '1.2.0', '1.2.1', '1.2.1+mini.084199244e3ab09cdf3e057a7751220e5f0b2ae7', '1.2.1+mini.89774df68d775dbf24055ae9aea5e8c69ec4ae62', '1.2.1+mini.a9b938ce25a11a3fedfac612a9b6682f9384337f', '1.2.1+mini.e1203ab110478d0edaea37aa29d484ef9ebd7451', '1.2.10', '1.2.10+mini.4c43dbe8f392067f5c2fc5828ed97640d22348f4', '1.2.10+mini.9ae0f7cb3cde10c7683bc0a32cc4a8b9cef232b3', '1.2.2', '1.2.2+mini.11964a8633739e8d36d21544c56b2e5832219d36', '1.2.2+mini.253724c80f2470a5b424c7e8397b65e7adf75b36', '1.2.2+mini.2917c1cec34688eb6998b91a5d57d15c3a5ad159', '1.2.3', '1.2.3+mini.b5ffbdc03dd6bf877531bde488d0b55fab8d0d99', '1.2.4', '1.2.4+mini.b9a32ef20bc76d15d1436440f442beff3e259360', '1.2.5', '1.2.5+mini.23a4d67fac911b10946620a3d87c888d415c56e9', '1.2.6', '1.2.6+mini.1548fe2b43c90a31121c01391e9c25cc8a075816', '1.2.7', '1.2.8', '1.2.8+mini.bc20474bbb0bae2018af65d0e97145d32c062d21', '1.2.9', '1.2.9+mini.bf21638a33dacf653be87fba689824525a41de14']

GATE_FILES = (
    'release_customer_followup_contract.py', 'release_customer_followup_security.py',
    'release_forward_bundle.py', 'release_upgrade.py', 'release_bundle_control.py',
    'release126_task_contract.py', 'release126_task_security.py',
    'release_visit_index_security.py', 'release122_visit_index_contract.py',
)
SECURITY_SECTIONS = {
    'relations', 'columns', 'functions', 'schemas', 'roles', 'memberships',
    'defaults', 'policies', 'database', 'database_role_settings',
    'modified_function_properties', 'constraints', 'triggers',
}
VALIDATION_FORMAT = 'salesbuddy-customer-followup-admission-v1'


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row['key']: row['sha256'] for row in migrations}
    require(target == 'V293' and len(migrations) == len(after) == 251
            and digest(migrations) == MIGRATION_PLAN_SHA and digest(after) == TARGET_LEDGER
            and 'V289' not in after and all(next((r for r in migrations if r['key'] == k), None)
                == {'key': k, 'file': v[0], 'sha256': v[1]} for k, v in SQL.items()),
            'Release 1.2.10 requires exactly the frozen V293 migration plan without V289')
    registered = list(registered)
    require(len(registered) == len(set(registered)), 'Duplicate migration registrations')
    complete = {'registered': sorted(registered), 'checksums': prior}
    if pending:
        require(source == 'V292' and pending == PENDING and len(prior) == 250
                and digest(prior) == BASE_LEDGER and len(registered) == 281
                and digest(sorted(registered)) == BASE_REGISTERED and digest(complete) == BASE_COMPLETE_LEDGER
                and set(after) == set(prior) | set(PENDING) and all(after[k] == v for k, v in prior.items()),
                'Only V293 may extend the unchanged complete production V292 ledger')
    else:
        require(source == 'V293' and prior == after and len(registered) == 282
                and digest(sorted(registered)) == TARGET_REGISTERED
                and digest(complete) == TARGET_COMPLETE_LEDGER, 'Final V293 complete ledger differs')


def validate(plan, migrations, *, digest, require):
    require(plan.get('product_version') == '1.2.10' and plan.get('release_scope') == RELEASE_SCOPE
            and plan.get('pending') == PENDING and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None
            and plan['expected_ledger'].get('running_jobs') == 0
            and plan.get('expected_database') == {'name': 'sales_saas', 'schema': 'V292', 'ledger_sha256': BASE_COMPLETE_LEDGER},
            'Exact idle V292 production and unchanged configuration required')
    revision = plan.get('candidate', {}).get('backend', {}).get('revision')
    require(_hex(revision, 40), 'A fixed backend release commit is required')
    require(plan.get('active_mini_versions') == ACTIVE_MINI_VERSIONS
            and plan.get('supported_mini_versions') == ACTIVE_MINI_VERSIONS,
            'Keep all 28 distributed clients unchanged; do not register a local Mini build')
    validate_migrations(migrations, plan['expected_ledger']['checksums'], plan['expected_ledger']['registered'],
                        plan['source_schema'], plan['target_schema'], plan['pending'], digest=digest, require=require)


def validate_admission(evidence, *, product, backend, web, clients, require):
    """Record scoped checks honestly, without claiming full end-to-end acceptance."""
    require(isinstance(evidence, dict) and evidence.get('format') == VALIDATION_FORMAT
            and product == '1.2.10' and evidence.get('release_scope') == RELEASE_SCOPE
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
    ci = evidence.get('cloud_ci')
    require(ci == 'passed' or (ci == 'not_run_billing_blocked'
            and evidence.get('ci_exception_authorization') == 'current_release_explicit_user_authorization'),
            'Cloud CI must pass or have this release explicit billing-blocked exception')
    for key in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'):
        ref = evidence.get(key, {})
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and _hex(ref.get('sha256'), 64),
                'Admission requires immutable source/checks/build/current-authorization receipts')


def validate_acl(plan, snapshot, *, digest, require):
    versions = plan.get('expected_registered_versions')
    require(plan.get('format') == 'salesbuddy-customer-followup-security-plan-v1'
            and plan.get('release_scope') == RELEASE_SCOPE and plan.get('mode') == 'customer_followup_exact_runtime_grants'
            and plan.get('maintenance_owner') == 'postgres'
            and plan.get('roles') == {'api': 'sales_api_saas', 'worker': 'sales_worker_saas',
                                     'feishu': 'sales_feishu_saas', 'platform': 'sales_platform_saas'}
            and plan.get('target') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(versions, list) and len(versions) == 282 and versions == sorted(set(versions))
            and digest(versions) == TARGET_REGISTERED, 'Exact V293 security plan required')
    fingerprints = snapshot.get('fingerprints', {})
    require(snapshot.get('format') == 'salesbuddy-live-security-v1' and set(fingerprints) == SECURITY_SECTIONS
            and all(_hex(v, 64) for v in fingerprints.values()) and _hex(snapshot.get('index_fingerprint'), 64)
            and snapshot.get('runtime_roles_nonprivileged') is True, 'Complete preserved production security required')


def validate_restore_evidence(evidence, objects, source_objects, *, digest, require):
    restored = evidence.get('restored_database', {})
    require(evidence.get('format') == 'salesbuddy-customer-followup-restore-rehearsal-v1'
            and evidence.get('status') == 'passed'
            and evidence.get('evidence_kind') == 'independent_production_restore'
            and evidence.get('source_database') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(restored, dict) and set(restored) == {'database', 'oid'}
            and isinstance(restored['database'], str) and restored['database'] != 'sales_saas'
            and isinstance(restored['oid'], int) and restored['oid'] != 18684
            and _hex(evidence.get('source_backup_sha256'), 64)
            and evidence.get('restored_from_production_backup') is True
            and evidence.get('source_schema') == 'V292' and evidence.get('target_schema') == 'V293'
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
    require(source.get('format') == 'salesbuddy-customer-followup-source-review-v1'
            and source.get('revision') == revision and source.get('static_syntax') == 'verified'
            and source.get('targeted_checks') == 'passed'
            and isinstance(source.get('check_commands'), list) and bool(source['check_commands'])
            and all(isinstance(c, str) and c.strip() for c in source['check_commands'])
            and _hex(source.get('check_receipt_sha256'), 64)
            and source.get('client_compatibility_method') == 'static_contract_review'
            and source.get('reviewed_mini_versions') == evidence['reviewed_mini_versions'],
            'Source receipt must identify final source, scoped checks and all preserved clients')
    require(build.get('format') == 'salesbuddy-customer-followup-build-receipt-v1'
            and build.get('revision') == revision and build.get('backend') == evidence['reviewed_backend']
            and build.get('web') == evidence['reviewed_web']
            and build.get('web_build') == 'completed' and build.get('mini_distribution') == 'unchanged',
            'Build receipt must identify paired artifacts and unchanged Mini distribution')
    require(authorization.get('format') == 'salesbuddy-customer-followup-human-authorization-v1'
            and authorization.get('release_scope') == RELEASE_SCOPE and authorization.get('revision') == revision
            and authorization.get('release_authorization') == evidence['release_authorization']
            and isinstance(authorization.get('release_authorization_message'), str)
            and bool(authorization['release_authorization_message'].strip())
            and authorization.get('cloud_ci') == evidence['cloud_ci'],
            'This release requires its own explicit human release decision and actual CI state')
    if evidence['cloud_ci'] != 'passed':
        require(authorization.get('ci_exception_authorization') == evidence['ci_exception_authorization']
                and isinstance(authorization.get('ci_exception_authorization_message'), str)
                and bool(authorization['ci_exception_authorization_message'].strip()),
                'The CI exception must refer to the current human authorization')
