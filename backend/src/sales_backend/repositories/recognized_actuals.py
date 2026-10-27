"""Narrow actual read, retaining the current company and object permissions."""

from sales_backend.db import json_value


async def read_recognized_actual(connection, opportunity_id, year, quarter):
    value = await connection.fetchval(
        "SELECT security.opportunity_recognized_actual($1::uuid,$2,$3)", str(opportunity_id), year, quarter
    )
    return json_value(value)
