"""Preview or enqueue bounded customer risk assessments using an existing administrator.

Reads deployment settings from process environment only; never reads .env files,
prints credentials/fact bodies, invokes a model, or executes queued worker jobs.
Preview is the default. Reuse its --request-id with --apply; replaying that ID
reuses the same assessment while facts are unchanged. Use a new request ID for
an explicit retry. Run from backend with PYTHONPATH=src.
"""

import argparse
import asyncio
import json
import sys
from uuid import UUID, uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata


class MaintenanceError(Exception):
    """Only fixed, non-sensitive error codes may be emitted by this CLI."""


def bounded_limit(value):
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 100") from exc
    if not 1 <= result <= 100:
        raise argparse.ArgumentTypeError("limit must be from 1 to 100")
    return result


def uuid_argument(value):
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("a UUID is required") from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="existing workspace external ID")
    parser.add_argument("--admin-account", required=True, help="existing active administrator account")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--customer-id", type=uuid_argument, action="append", help="repeat to select customer UUIDs")
    selection.add_argument(
        "--all-authorized", action="store_true", help="consider all authorized customers, bounded by limit",
    )
    parser.add_argument("--limit", type=bounded_limit, default=20)
    parser.add_argument("--request-id", type=uuid_argument, default=None, help="request UUID for idempotent replay")
    parser.add_argument("--apply", action="store_true", help="enqueue in one transaction; default only previews")
    args = parser.parse_args(argv)
    args.workspace = args.workspace.strip()
    args.admin_account = args.admin_account.strip()
    if not args.workspace or not args.admin_account:
        parser.error("workspace and admin-account must not be blank")
    args.customer_id = sorted(set(args.customer_id or []))
    if len(args.customer_id) > args.limit:
        parser.error("explicit customer count exceeds limit; raise --limit up to 100")
    args.request_id = args.request_id or str(uuid4())
    return args


async def execute(database, args):
    async with database.connection() as connection:
        async with connection.transaction(readonly=True):
            unsafe = await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            if unsafe:
                raise MaintenanceError("RISK_MAINTENANCE_REQUIRES_RLS_RUNTIME")
            identity = await IdentityRepository().find_maintenance_administrator(
                connection, workspace_external_id=args.workspace, account_code=args.admin_account,
            )
    if identity is None or identity.context.role != RoleCode.ADMINISTRATOR:
        raise MaintenanceError("RISK_MAINTENANCE_ADMINISTRATOR_REQUIRED")
    actor = identity.context
    async with database.transaction(actor, readonly=not args.apply) as connection:
        await connection.execute("SET LOCAL statement_timeout='20s'")
        await connection.execute("SET LOCAL lock_timeout='5s'")
        rows = await connection.fetch(
            "SELECT c.id::text AS customer_id,c.name AS customer_name,"
            "security.customer_risk_assessment_owner(c.id)::text AS owner_user_ref_id,"
            "latest.id::text AS latest_assessment_id,latest.status AS latest_status,"
            "latest.outcome AS latest_outcome "
            "FROM crm.customer c LEFT JOIN LATERAL ("
            "SELECT id,status,outcome FROM insight.customer_risk_assessment a "
            "WHERE a.workspace_id=c.workspace_id AND a.customer_id=c.id "
            "ORDER BY a.created_at DESC,a.id DESC LIMIT 1) latest ON true "
            "WHERE c.workspace_id=$1::uuid AND c.deleted_at IS NULL "
            "AND ($2::uuid[] IS NULL OR c.id=ANY($2::uuid[])) "
            "ORDER BY c.id LIMIT $3",
            actor.workspace_id, args.customer_id or None, args.limit + 1,
        )
        has_more = len(rows) > args.limit
        selected = [dict(row) for row in rows[:args.limit]]
        requested = set(args.customer_id)
        missing = sorted(requested - {row["customer_id"] for row in selected})
        if args.apply and missing:
            raise MaintenanceError("RISK_MAINTENANCE_CUSTOMER_UNAVAILABLE")
        assessment_ids, job_ids = [], []
        for row in selected:
            row["action"] = "preview" if row["owner_user_ref_id"] else "skipped_unclaimed"
            if not args.apply or not row["owner_user_ref_id"]:
                continue
            assessment_id = await enqueue_customer_risk_review(
                connection, actor, customer_id=row["customer_id"],
                trigger_type="maintenance.customer_risk", trigger_id=args.request_id,
            )
            if assessment_id is None:
                row["action"] = "skipped_no_active_owner"
                continue
            receipt = await connection.fetchrow(
                "SELECT a.id::text AS assessment_id,a.job_id::text AS job_id,a.status AS assessment_status,"
                "a.actor_user_ref_id::text AS execution_owner_user_ref_id,j.status AS job_status "
                "FROM insight.customer_risk_assessment a LEFT JOIN ops.job j "
                "ON j.id=a.job_id AND j.workspace_id=a.workspace_id "
                "WHERE a.id=$1::uuid AND a.workspace_id=$2::uuid",
                assessment_id, actor.workspace_id,
            )
            if receipt is None or receipt["job_id"] is None:
                raise MaintenanceError("RISK_MAINTENANCE_QUEUE_RECEIPT_MISSING")
            row.update(dict(receipt))
            row["action"] = "queued_or_reused"
            assessment_ids.append(receipt["assessment_id"])
            job_ids.append(receipt["job_id"])
        return {
            "mode": "apply" if args.apply else "preview", "workspace": args.workspace,
            "request_id": args.request_id, "limit": args.limit,
            "selected_count": len(selected), "has_more": has_more, "unavailable_customer_ids": missing,
            "assessment_ids": assessment_ids, "job_ids": job_ids, "customers": selected,
        }


async def run(args):
    database = Database(get_settings())
    metadata = request_metadata.set(RequestMetadata(request_id=args.request_id, user_agent="customer-risk-maintenance"))
    try:
        await database.connect()
        result = await execute(database, args)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        request_metadata.reset(metadata)
        try:
            await asyncio.wait_for(database.close(), timeout=5)
        except TimeoutError:
            if database.pool is not None:
                database.pool.terminate()


async def bounded_run(args):
    try:
        await asyncio.wait_for(run(args), timeout=90)
        return 0
    except MaintenanceError as exc:
        code = str(exc)
    except TimeoutError:
        code = "RISK_MAINTENANCE_TIMEOUT"
    except Exception:
        # No exception text, connection strings, private facts or provider keys.
        code = "RISK_MAINTENANCE_FAILED"
    print(json.dumps({"status": "failed", "code": code}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(bounded_run(parse_args())))
