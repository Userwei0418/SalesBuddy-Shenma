"""Creation-specific references, isolated from browsing and follow-up directories."""
import asyncpg

from sales_backend.db import json_value
from sales_backend.domain.opportunity_creation import translate_creation_error


async def creation_access(connection, customer_id):
    result = json_value(await connection.fetchval(
        "SELECT security.opportunity_create_customer_access($1::uuid)", str(customer_id)))
    reference = result.get("customer_reference")
    if reference:
        result["customer_reference"] = {key: reference[key] for key in ("id", "name")}
    return result


async def creation_customers(connection, query, limit, offset):
    return json_value(await connection.fetchval(
        "SELECT security.opportunity_create_customer_options($1,$2,$3)", query, limit, offset))


async def require_creation_customer(connection, customer_id):
    try:
        return json_value(await connection.fetchval(
            "SELECT security.require_opportunity_create_customer($1::uuid)", str(customer_id)))
    except asyncpg.PostgresError as error:
        translate_creation_error(error)
