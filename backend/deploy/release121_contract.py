"""Exact 1.2.1 successor to the observed V238/201 r14 production ledger.

V234 is intentionally applied below the current head. This exception admits
only the fixed 201-to-202 transition, never arbitrary historical SQL. Digests
are nonsecret source admission evidence, not proof of a database deployment.
"""

CONTRACT_ID = 'salesbuddy-release121-v1'
RELEASE_SCOPE = ['release_1.2.1']
PENDING = ['V234']
BASE_LEDGER = '632509db144d3ef96ff12ef3eb567a93275e193ac42a0c4296bb92ffe6f99651'
BASE_REGISTERED = '050816e96c3800784abc9e333fe2ffca6021c74fd85763ceab429539f76a34df'
TARGET_LEDGER = '1218355e3aeefab74d0a373924837555a941a964ac752fec20d8fc6d11b8ecc8'
TARGET_REGISTERED = '71572cfc10c5cd1dcb22cd1e59b2ef43f1c2f51058a125e0d543264e1294c885'
SERVICES_SHA = '7a428874bf6a1bbed2ce2a1ba1208b717b99a0178ece861e3da9b191d911b355'
POLICY_SHA = 'a8f44ff7fb1b4bc342d5bafcc84e3494006c7f639dc6799a24b5272b09ee028e'
SQL = {'V234': ('migrations/V234__opportunity_product_line_intents.sql',
                '55131d90466fc14e9f53209720d7202ef80abd92a25f8a48e3332fe11ea8663c')}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    """Admit the exact pre-migration state or the exact fully applied state."""
    require(source == target == 'V238', '1.2.1 requires the exact V238-to-V238 transition')
    after = {row['key']: row['sha256'] for row in migrations}
    require(len(migrations) == len(after) == 202 and digest(after) == TARGET_LEDGER
            and {row['key']: (row.get('file'), row['sha256'])
                 for row in migrations if row['key'] in PENDING} == SQL,
            '1.2.1 requires exactly the fixed 202 checksums and reviewed V234 SQL')
    registered = list(registered)
    require(len(registered) == len(set(registered)), '1.2.1 registered ledger has duplicates')
    if pending:
        require(pending == PENDING and len(prior) == 201 and digest(prior) == BASE_LEDGER
                and len(registered) == 232 and digest(sorted(registered)) == BASE_REGISTERED
                and set(after) == set(prior) | set(PENDING)
                and all(after[key] == value for key, value in prior.items()),
                '1.2.1 requires the exact unchanged r14 ledger and only V234 pending')
    else:
        require(prior == after and len(registered) == 233
                and digest(sorted(registered)) == TARGET_REGISTERED,
                '1.2.1 requires the exact completed 202 checksum and 233 registered ledger')


def validate(plan, migrations, *, digest, require):
    require(plan.get('product_version') == '1.2.1' and plan.get('release_scope') == RELEASE_SCOPE
            and plan['pending'] == PENDING and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None,
            '1.2.1 must use its fixed scope, only V234 and unchanged Agent configuration')
    validate_migrations(migrations, plan['expected_ledger']['checksums'],
                        plan['expected_ledger']['registered'], plan['source_schema'],
                        plan['target_schema'], plan['pending'], digest=digest, require=require)


def validate_acl(plan, policy, *, digest, require):
    versions = plan.get('expected_registered_versions')
    require(plan.get('release_scope') == RELEASE_SCOPE
            and isinstance(versions, list) and len(versions) == 233
            and versions == sorted(set(versions)) and digest(versions) == TARGET_REGISTERED,
            '1.2.1 ACL must target the exact V238/202 registered ledger')
    require(policy.get('schema') == 'V238' and policy.get('migration_fingerprint') == TARGET_LEDGER
            and digest(policy.get('services')) == SERVICES_SHA and digest(policy) == POLICY_SHA,
            '1.2.1 ACL must target the exact accepted normal permission policy')
