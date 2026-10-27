"""Read-only opportunity visibility, independent of recording and edit grants."""
from sales_backend.repositories.authorization import AuthorizationRepository


async def available(connection):
    return await connection.fetchval('SELECT security.sales_shared_opportunity_actor()') is True


async def require_read_feature(connection, permissions=None):
    permissions = permissions or await AuthorizationRepository().effective(connection)
    if not permissions.allows('opportunity.read') and not await available(connection):
        permissions.require('opportunity.read')
    return permissions
