"""Exact V288 -> V292 business release; V289 is deliberately absent."""

RELEASE_SCOPE = ['release_1.2.10_business_fixes']
PENDING = ['V290', 'V291', 'V292']
BASE_LEDGER = 'e645279ad599f927ee72a52722b59de60194522e4fe8d0bfb472e7e767e79600'
BASE_REGISTERED = 'b95132178440e2efd4697dd0a13b6dc101d9bc93df21d77a065469cae9de0c3e'
BASE_COMPLETE_LEDGER = '6d409cc6bb74b09cea61dc615f8a19647f879f1416cd5a0e2d026ea5a4301e92'
TARGET_LEDGER = '6854f9e95825d11a20095dbf26ef0bf5486f28f38f732e426a8ec89448dde352'
TARGET_REGISTERED = '523e08511b97b3a1095dacbf85d5794046420f9a95b9488cd35138accb6ec79f'
TARGET_COMPLETE_LEDGER = '9205a5f174475c384dd8a847333920e3b27dc7207efae28404255997e3022de1'
MIGRATION_PLAN_SHA = '29d5314f2d3be684af663b09d3d7b1f0f0c95f4cb2591e6c7620136e36e01c22'
INSTALLATION_POLICY_SHA = 'aaa69289ab45b9b6b3d9d06c3d96060b6f4c25b5aec502861f82618338b4641a'
SQL = {'V290': ('migrations/V290__opportunity_collection_direct_edit.sql', 'da82182a0154e5fc2e1fa13b920ad8e44a0f70d509ca81d210098bc6638a99fc'), 'V291': ('migrations/V291__company_opportunity_follow_up.sql', '862b270bfa03ba5eaa6933c5faf6af1e29d2a0623ca93ac8d1adb65ebc84331a'), 'V292': ('migrations/V292__sales_opportunity_creation_visibility.sql', '3b40f5dd34cd24203baea8b55a9dbf58955d5b08eff26428161095c1a83989e8')}
ACTIVE_MINI_VERSIONS = ['1.1.1', '1.1.2', '1.2.0', '1.2.1', '1.2.1+mini.084199244e3ab09cdf3e057a7751220e5f0b2ae7', '1.2.1+mini.89774df68d775dbf24055ae9aea5e8c69ec4ae62', '1.2.1+mini.a9b938ce25a11a3fedfac612a9b6682f9384337f', '1.2.1+mini.e1203ab110478d0edaea37aa29d484ef9ebd7451', '1.2.2', '1.2.2+mini.11964a8633739e8d36d21544c56b2e5832219d36', '1.2.2+mini.253724c80f2470a5b424c7e8397b65e7adf75b36', '1.2.2+mini.2917c1cec34688eb6998b91a5d57d15c3a5ad159', '1.2.3', '1.2.3+mini.b5ffbdc03dd6bf877531bde488d0b55fab8d0d99', '1.2.4', '1.2.4+mini.b9a32ef20bc76d15d1436440f442beff3e259360', '1.2.5', '1.2.5+mini.23a4d67fac911b10946620a3d87c888d415c56e9', '1.2.6', '1.2.6+mini.1548fe2b43c90a31121c01391e9c25cc8a075816', '1.2.7', '1.2.8', '1.2.8+mini.bc20474bbb0bae2018af65d0e97145d32c062d21', '1.2.9', '1.2.9+mini.bf21638a33dacf653be87fba689824525a41de14']

GATE_FILES = (
    'release1210_business_contract.py', 'release1210_business_security.py',
    'release_forward_bundle.py', 'release_upgrade.py', 'release_bundle_control.py',
    'release126_task_contract.py', 'release126_task_security.py',
    'release_visit_index_security.py', 'release122_visit_index_contract.py',
)
SECURITY_SECTIONS = {
    'relations', 'columns', 'functions', 'schemas', 'roles', 'memberships',
    'defaults', 'policies', 'database', 'database_role_settings',
    'modified_function_properties', 'constraints', 'triggers',
}
VALIDATION_FORMAT = 'salesbuddy-release1210-static-admission-v1'


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in '0123456789abcdef' for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row['key']: row['sha256'] for row in migrations}
    require(target == 'V292' and len(migrations) == len(after) == 250
            and digest(migrations) == MIGRATION_PLAN_SHA and digest(after) == TARGET_LEDGER
            and 'V289' not in after and all(next((r for r in migrations if r['key'] == k), None)
                == {'key': k, 'file': v[0], 'sha256': v[1]} for k, v in SQL.items()),
            'Release 1.2.10 requires exactly the frozen V292 migration plan without V289')
    registered = list(registered)
    require(len(registered) == len(set(registered)), 'Duplicate migration registrations')
    complete = {'registered': sorted(registered), 'checksums': prior}
    if pending:
        require(source == 'V288' and pending == PENDING and len(prior) == 247
                and digest(prior) == BASE_LEDGER and len(registered) == 278
                and digest(sorted(registered)) == BASE_REGISTERED and digest(complete) == BASE_COMPLETE_LEDGER
                and set(after) == set(prior) | set(PENDING) and all(after[k] == v for k, v in prior.items()),
                'Only V290-V292 may extend the unchanged complete production V288 ledger')
    else:
        require(source == 'V292' and prior == after and len(registered) == 281
                and digest(sorted(registered)) == TARGET_REGISTERED
                and digest(complete) == TARGET_COMPLETE_LEDGER, 'Final V292 complete ledger differs')


def validate(plan, migrations, *, digest, require):
    require(plan.get('product_version') == '1.2.10' and plan.get('release_scope') == RELEASE_SCOPE
            and plan.get('pending') == PENDING and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None
            and plan['expected_ledger'].get('running_jobs') == 0
            and plan.get('expected_database') == {'name': 'sales_saas', 'schema': 'V288', 'ledger_sha256': BASE_COMPLETE_LEDGER},
            'Exact idle V288 production and unchanged configuration required')
    revision = plan.get('candidate', {}).get('backend', {}).get('revision')
    mini = plan.get('mini_source_revision')
    require(_hex(revision, 40) and mini == revision, 'Backend and Mini must use the same fixed release commit')
    active, supported = plan.get('active_mini_versions'), plan.get('supported_mini_versions')
    require(active == ACTIVE_MINI_VERSIONS and isinstance(supported, list)
            and len(supported) == len(set(supported))
            and set(supported) == set(ACTIVE_MINI_VERSIONS) | {'1.2.10', '1.2.10+mini.' + mini},
            'Preserve every frozen production client and add only the exact 1.2.10 build')
    validate_migrations(migrations, plan['expected_ledger']['checksums'], plan['expected_ledger']['registered'],
                        plan['source_schema'], plan['target_schema'], plan['pending'], digest=digest, require=require)


def validate_admission(evidence, *, product, backend, web, clients, require):
    """A user-authorized static admission is never labelled a business test."""
    require(isinstance(evidence, dict) and evidence.get('format') == VALIDATION_FORMAT
            and product == '1.2.10' and evidence.get('release_scope') == RELEASE_SCOPE
            and evidence.get('status') == 'statically_reviewed'
            and evidence.get('business_tests') == 'not_run_user_requested'
            and evidence.get('human_acceptance') == 'pending'
            and evidence.get('cloud_ci') == 'not_run_billing_blocked'
            and evidence.get('release_authorization') == 'current_release_explicit_user_authorization'
            and evidence.get('ci_exception_authorization') == 'current_release_explicit_user_authorization'
            and evidence.get('reviewed_backend') == backend and evidence.get('reviewed_web') == web
            and _hex(backend.get('revision'), 40)
            and evidence.get('mini_source_revision') == backend['revision']
            and _hex(evidence.get('mini_artifact_sha256'), 64)
            and evidence.get('static_syntax') == 'verified' and evidence.get('web_build') == 'completed'
            and evidence.get('mini_build') == 'completed'
            and evidence.get('client_compatibility_method') == 'static_contract_review'
            and isinstance(evidence.get('reviewed_mini_versions'), list)
            and set(evidence['reviewed_mini_versions']) == set(clients)
            == set(ACTIVE_MINI_VERSIONS) | {'1.2.10', '1.2.10+mini.' + backend['revision']}
            and not any(key.startswith('tested_') for key in evidence),
            'Release 1.2.10 requires honest fixed-source static/build admission and current explicit CI exception')
    for key in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'):
        ref = evidence.get(key, {})
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and _hex(ref.get('sha256'), 64),
                'Static admission requires immutable source/build/current-authorization receipts')


def validate_acl(plan, snapshot, *, digest, require):
    versions = plan.get('expected_registered_versions')
    require(plan.get('format') == 'salesbuddy-release1210-security-plan-v1'
            and plan.get('release_scope') == RELEASE_SCOPE and plan.get('mode') == 'business_exact_runtime_grants'
            and plan.get('maintenance_owner') == 'postgres'
            and plan.get('roles') == {'api': 'sales_api_saas', 'worker': 'sales_worker_saas',
                                     'feishu': 'sales_feishu_saas', 'platform': 'sales_platform_saas'}
            and plan.get('target') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(versions, list) and len(versions) == 281 and versions == sorted(set(versions))
            and digest(versions) == TARGET_REGISTERED, 'Exact V292 security plan required')
    fingerprints = snapshot.get('fingerprints', {})
    require(snapshot.get('format') == 'salesbuddy-live-security-v1' and set(fingerprints) == SECURITY_SECTIONS
            and all(_hex(v, 64) for v in fingerprints.values()) and _hex(snapshot.get('index_fingerprint'), 64)
            and snapshot.get('runtime_roles_nonprivileged') is True, 'Complete preserved production security required')


def validate_restore_evidence(evidence, objects, source_objects, *, digest, require):
    restored = evidence.get('restored_database', {})
    require(evidence.get('format') == 'salesbuddy-release1210-restore-rehearsal-v1'
            and evidence.get('status') == 'passed'
            and evidence.get('evidence_kind') == 'independent_production_restore'
            and evidence.get('source_database') == {'database': 'sales_saas', 'oid': 18684}
            and isinstance(restored, dict) and set(restored) == {'database', 'oid'}
            and isinstance(restored['database'], str) and restored['database'] != 'sales_saas'
            and isinstance(restored['oid'], int) and restored['oid'] != 18684
            and _hex(evidence.get('source_backup_sha256'), 64)
            and evidence.get('restored_from_production_backup') is True
            and evidence.get('source_schema') == 'V288' and evidence.get('target_schema') == 'V292'
            and evidence.get('source_ledger_sha256') == BASE_COMPLETE_LEDGER
            and evidence.get('target_ledger_sha256') == TARGET_COMPLETE_LEDGER
            and evidence.get('migration_plan_sha256') == MIGRATION_PLAN_SHA
            and evidence.get('new_objects_sha256') == digest(objects)
            and evidence.get('source_objects_sha256') == digest(source_objects)
            and evidence.get('security_preserved') is True and evidence.get('business_state_preserved') is True
            and evidence.get('effective_privileges_verified') is True,
            'A fresh matching production-backup restore and exact security/object evidence are required')


def validate_admission_receipts(evidence, receipts, *, require):
    revision = evidence['mini_source_revision']
    source, build, authorization = (receipts[k] for k in ('source_review_ref', 'build_receipt_ref', 'authorization_ref'))
    require(source.get('format') == 'salesbuddy-release1210-source-review-v1'
            and source.get('revision') == revision and source.get('static_syntax') == 'verified'
            and source.get('client_compatibility_method') == 'static_contract_review'
            and source.get('reviewed_mini_versions') == evidence['reviewed_mini_versions'],
            'Source review receipt must identify the final source and exact reviewed clients')
    require(build.get('format') == 'salesbuddy-release1210-build-receipt-v1'
            and build.get('revision') == revision and build.get('backend') == evidence['reviewed_backend']
            and build.get('web') == evidence['reviewed_web']
            and build.get('mini_artifact_sha256') == evidence['mini_artifact_sha256']
            and build.get('web_build') == 'completed' and build.get('mini_build') == 'completed',
            'Build receipt must identify the exact paired artifacts')
    require(authorization.get('format') == 'salesbuddy-release1210-human-authorization-v1'
            and authorization.get('release_scope') == RELEASE_SCOPE and authorization.get('revision') == revision
            and authorization.get('release_authorization') == evidence['release_authorization']
            and authorization.get('ci_exception_authorization') == evidence['ci_exception_authorization']
            and isinstance(authorization.get('release_authorization_message'), str)
            and bool(authorization['release_authorization_message'].strip())
            and isinstance(authorization.get('ci_exception_authorization_message'), str)
            and bool(authorization['ci_exception_authorization_message'].strip()),
            'This release requires its own explicit human release and CI-exception decisions')
