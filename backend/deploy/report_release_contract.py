"""Exact report-only production successor to r11; no finance or export migration.

These nonsecret digests bind the observed V233 production ledger to the four
already reviewed report SQL files. They are not a database or deployment proof.
"""

PENDING = ['V235', 'V236', 'V237', 'V238']
BASE_LEDGER = '4838fdac5ab18e821bd51e794b63b179f9ef467e3ea2d47e961426603bf417a4'
BASE_REGISTERED = '51c8d2b81183bc70d6b3a28a172d44f036dc2dc5005d66fd7d0ac118bf596b6e'
TARGET_LEDGER = '632509db144d3ef96ff12ef3eb567a93275e193ac42a0c4296bb92ffe6f99651'
TARGET_REGISTERED = '050816e96c3800784abc9e333fe2ffca6021c74fd85763ceab429539f76a34df'
SERVICES_SHA = '7a428874bf6a1bbed2ce2a1ba1208b717b99a0178ece861e3da9b191d911b355'
SQL = {
    'V235': ('migrations/V235__work_report_meeting_facts.sql', '9f9d6453d45327f820004341588d01b3c0032a5dc684676e6417c3d3eb0dd048'),
    'V236': ('migrations/V236__work_report_overview_freeze.sql', 'c018bd6351b1dc9105999007cbb9a27e932007e74ea3610d47cb9948a94b51a1'),
    'V237': ('migrations/V237__work_report_exact_generation_version.sql', '5925f37840577a57e63e7e098a447e651cfc0ec3bc18a144a69e7d7a02e582f1'),
    'V238': ('migrations/V238__work_report_point_snapshot_access.sql', '529c8933a5ecf54e8590a357873d2e7adc598beb3ca55599f72774e3c41ca030'),
}


def validate_acl(plan, policy, *, digest, require):
    """Reject a valid but wrong-schema ACL plan before any SQL is attempted."""
    versions = plan.get('expected_registered_versions')
    require(plan.get('release_scope') == ['work_report']
            and isinstance(versions, list) and len(versions) == 232
            and versions == sorted(set(versions))
            and digest(versions) == TARGET_REGISTERED,
            'Report-only ACL must target the exact V238 registered ledger')
    require(policy.get('schema') == 'V238'
            and policy.get('migration_fingerprint') == TARGET_LEDGER
            and digest(policy.get('services')) == SERVICES_SHA,
            'Report-only ACL must target the exact V238 report permission policy')


def validate(plan, migrations, *, digest, require):
    require(plan.get('release_scope') == ['work_report']
            and plan['pending'] == PENDING
            and plan.get('agent_stage') is None
            and plan.get('prepared_configuration_delta') is None,
            'Report-only release must preserve Agent configuration and use exactly four report migrations')
    before = plan['expected_ledger']['checksums']
    registered = plan['expected_ledger']['registered']
    after = {row['key']: row['sha256'] for row in migrations}
    require(len(before) == 197 and digest(before) == BASE_LEDGER
            and len(registered) == 228 and digest(sorted(registered)) == BASE_REGISTERED,
            'Exact r11 V233 production ledger required')
    require(len(after) == len(migrations) == 201 and digest(after) == TARGET_LEDGER
            and set(after) == set(before) | set(PENDING)
            and all(after[key] == value for key, value in before.items()),
            'Report-only ledger must preserve every old checksum and exclude V234/V239')
    require({row['key']: (row['file'], row['sha256']) for row in migrations if row['key'] in PENDING} == SQL,
            'Reviewed report SQL changed')

