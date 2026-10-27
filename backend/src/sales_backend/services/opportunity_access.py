"""Current access projections; legacy collaboration management is retired."""

from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.repositories.opportunity_access import OpportunityAccessRepository
from sales_backend.services.opportunity_transfer import transfer_capability


def collaboration_retired():
    raise OpportunityAccessError(
        "OPPORTUNITY_COLLABORATION_RETIRED", "商机协作名单设置已停用，请刷新页面", status=410,
    )


def without_collaboration_management(projection):
    projection["capabilities"]["can_manage_collaborators"] = False
    return projection


async def with_transfer_capability(connection, projection):
    capability = await transfer_capability(connection, projection["opportunity_id"])
    projection["capabilities"]["can_transfer"] = capability["can_transfer"]
    projection["capability_reasons"]["transfer"] = capability["reason"]
    return without_collaboration_management(projection)


async def read_access(connection, opportunity_id):
    projection = await OpportunityAccessRepository().access(connection, opportunity_id)
    return await with_transfer_capability(connection, projection)


async def read_options(connection, *, query, customer_id, limit, offset, order="name"):
    page = await OpportunityAccessRepository().options(
        connection, query=query, customer_id=customer_id, limit=limit, offset=offset, order=order)
    # The opt-in batch query hydrates the same transfer capability for its page
    # in SQL, without one extra database round trip per recommendation.
    if order == "recent_follow_up":
        for projection in page["items"]:
            without_collaboration_management(projection)
        return page
    for projection in page["items"]:
        await with_transfer_capability(connection, projection)
    return page


async def set_collaborators(connection, actor, opportunity_id, body, key):
    # Keep the old service signature, but never read/replay receipts or write.
    collaboration_retired()
