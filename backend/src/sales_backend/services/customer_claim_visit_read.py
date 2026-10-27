"""Current customer ownership grants only that customer's formal visit reads."""


async def available(connection):
    return await connection.fetchval('SELECT security.customer_claim_visit_reader()') is True
