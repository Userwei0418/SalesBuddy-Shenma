"""Human-confirmed collaboration mutations with aggregate versioning."""

from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.collaboration import (
    bump_membership_version,
    effective_ids,
    manageable_version,
    members_by_opportunity,
    validated_fde,
    write_members,
)


async def set_opportunity_members(connection, actor, opportunity_id, ids, version):
    current_version = await manageable_version(connection, opportunity_id)
    require_version(current_version, version)
    before = await effective_ids(connection, opportunity_id)
    desired = {r["id"] for r in await validated_fde(connection, ids, existing=before)}
    changed = before != desired
    if changed:
        new_version = await bump_membership_version(connection, opportunity_id, version)
        if new_version is None:
            # Same error contract as all other optimistic aggregate writes.
            require_version(-1, version)
        primary = await connection.fetchval("SELECT primary_fde_id::text FROM crm.opportunity WHERE id=$1::uuid", opportunity_id)
        if primary and primary not in desired:
            raise ValueError("主责FDE须保留在长期名单中，请用人员面板一并更换或清空")
        await write_members(connection, actor, opportunity_id, desired)
    else:
        new_version = current_version
    return {
        "id": opportunity_id,
        "version_no": new_version,
        "changed": changed,
        "fde_members": (await members_by_opportunity(connection, [opportunity_id])).get(opportunity_id, []),
    }
