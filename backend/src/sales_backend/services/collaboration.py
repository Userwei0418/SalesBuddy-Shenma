"""Human-confirmed collaboration mutations with aggregate versioning."""

from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.collaboration import (
    effective_ids,
    manageable_version,
    members_by_opportunity,
    validated_fde,
)


async def set_opportunity_members(connection, actor, opportunity_id, ids, version):
    current_version = await manageable_version(connection, opportunity_id)
    require_version(current_version, version)
    before = await effective_ids(connection, opportunity_id)
    desired = {r["id"] for r in await validated_fde(connection, ids, existing=before)}
    primary = await connection.fetchval("SELECT primary_fde_id::text FROM crm.opportunity WHERE id=$1::uuid", opportunity_id)
    if primary and primary not in desired:
        raise ValueError("主责FDE须保留在长期名单中，请用人员面板一并更换或清空")
    # V280 checks even a no-op under the aggregate lock: the version or roster
    # may change after the reads above. It also derives the complete history.
    new_version = await connection.fetchval(
        "SELECT security.set_fde_members_with_history($1::uuid,$2::integer,$3::uuid[])",
        opportunity_id, version, sorted(desired),
    )
    if new_version is None:
        require_version(-1, version)
    return {
        "id": opportunity_id,
        "version_no": new_version,
        "changed": new_version != current_version,
        "fde_members": (await members_by_opportunity(connection, [opportunity_id])).get(opportunity_id, []),
    }
