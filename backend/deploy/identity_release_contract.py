"""Exact enterprise-identity V238/202 -> V239/203 production admission.

Source admission only. Actual deployment still requires the existing complete
ledger, backup, service, client, configuration and source-closure checks.
"""

from release121_contract import TARGET_LEDGER as BASE_LEDGER
from release121_contract import TARGET_REGISTERED as BASE_REGISTERED

RELEASE_SCOPE = ['enterprise_identity_1.2.1']
PENDING = ['V239']
TARGET_LEDGER = '9819f3a337422867de94bcde127f4a2473849fc908caec362484757c35dc7c0b'
TARGET_REGISTERED = '4a1f28cc8a715f663c1dec12c29783fc8e95d3505fa2dc5be20edd8b140c9832'
SERVICES_SHA = '9c4dbc55dfc4cae0ea5584fcc1ed32b022ee581e9cae76105bec46f9e10fe233'
POLICY_SHA = '27c128ff6bf3481d63599bef03c7163ccde043b9e3ffa0e08ffa88fd35fb91dc'
SQL = {'V239': ('migrations/V239__enterprise_business_identities.sql',
                'c8ec91a8e782919c8b182bac1e4fe8a3a6022e889627088e5429c736d9136934')}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row['key']: row['sha256'] for row in migrations}
    require(target == 'V239' and len(migrations) == len(after) == 203
            and digest(after) == TARGET_LEDGER
            and {row['key']: (row.get('file'), row['sha256']) for row in migrations if row['key'] in PENDING} == SQL,
            'Identity release requires exactly the reviewed 203 checksums and V239 SQL')
    registered = list(registered)
    require(len(registered) == len(set(registered)), 'Identity registered ledger has duplicates')
    if pending:
        require(source == 'V238' and pending == PENDING and len(prior) == 202
                and digest(prior) == BASE_LEDGER and len(registered) == 233
                and digest(sorted(registered)) == BASE_REGISTERED
                and set(after) == set(prior) | set(PENDING)
                and all(after[key] == value for key,value in prior.items()),
                'Identity upgrade requires the unchanged 202/233 production ledger and only V239 pending')
    else:
        require(source == 'V239' and prior == after and len(registered) == 234
                and digest(sorted(registered)) == TARGET_REGISTERED,
                'Identity final state requires exact 203 checksums and 234 registrations')


def validate(plan, migrations, *, digest, require):
    require(plan.get('product_version') == '1.2.1' and plan.get('release_scope') == RELEASE_SCOPE
            and plan['pending'] == PENDING and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None,
            'Identity release requires fixed scope, only V239 and unchanged Agent configuration')
    validate_migrations(migrations,plan['expected_ledger']['checksums'],plan['expected_ledger']['registered'],
                        plan['source_schema'],plan['target_schema'],plan['pending'],digest=digest,require=require)


def validate_acl(plan, policy, *, digest, require):
    versions = plan.get('expected_registered_versions')
    require(plan.get('release_scope') == RELEASE_SCOPE and isinstance(versions,list)
            and len(versions) == 234 and versions == sorted(set(versions))
            and digest(versions) == TARGET_REGISTERED,
            'Identity ACL requires the exact 234 production registrations')
    require(policy.get('schema') == 'V239' and policy.get('migration_fingerprint') == TARGET_LEDGER
            and digest(policy.get('services')) == SERVICES_SHA and digest(policy) == POLICY_SHA,
            'Identity ACL requires the exact API-only three-function policy delta')
