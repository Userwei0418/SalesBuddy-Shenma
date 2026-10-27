"""All authorization, locks, receipts and evidence are inside reviewed SQL."""

from sales_backend.db import json_value


class OpportunityTransferRepository:
    async def capability(self, connection, opportunity_id):
        return json_value(
            await connection.fetchval("SELECT security.opportunity_transfer_capability($1::uuid)", opportunity_id)
        )

    async def candidates(self, connection, opportunity_id, *, query, limit, offset):
        return json_value(
            await connection.fetchval(
                "SELECT security.opportunity_transfer_candidates($1::uuid,$2::text,$3::integer,$4::integer)",
                opportunity_id,
                query,
                limit,
                offset,
            )
        )

    async def transfer(self, connection, opportunity_id, body, key):
        return json_value(
            await connection.fetchval(
                "SELECT security.transfer_opportunity_owner($1::uuid,$2::integer,$3::uuid,$4::uuid,$5::text,$6::uuid)",
                opportunity_id,
                body.version_no,
                body.new_owner_user_ref_id,
                body.new_owner_team_id,
                body.reason,
                key,
            )
        )
