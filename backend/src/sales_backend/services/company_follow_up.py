"""Independent recording capability; never substitutes object read/edit grants."""

RECORDING_ACTIONS = frozenset({
    'visit.create', 'visit.structure', 'visit.quality_review',
    'visit.upload', 'visit.transcribe', 'visit.retry_import',
})


async def available(connection):
    return await connection.fetchval('SELECT security.company_opportunity_follow_up_actor()') is True


async def customer_available(connection):
    return await connection.fetchval('SELECT security.company_customer_follow_up_actor()') is True


async def permits_action(connection, permission):
    return permission in RECORDING_ACTIONS and await available(connection)
