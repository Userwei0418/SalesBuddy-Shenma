"""Bounded formal-field interfaces shared by Mini and Web."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.opportunity_fields import OpportunityFieldPatch, OpportunityPeoplePatch
from sales_backend.contracts.opportunity_intent_history import OpportunityIntentHistory
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.opportunity_field_catalog import DEFINITION_VERSION, PARTICIPANT_ROLES, field_catalog
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.opportunity_fields import editable_fields, member_directory, read_fields
from sales_backend.repositories.opportunity_intent_history import read_intent_history
from sales_backend.services.opportunity_fields import patch_fields
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1", tags=["Opportunity business fields"])


@router.get("/opportunities/field-options")
async def options(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        permissions = await AuthorizationRepository().effective(connection)
        permissions.require("opportunity.read")
        permitted = editable_fields(can_edit=permissions.allows("opportunity.update"),
                                    can_fde=permissions.allows("opportunity.fde_members"))
        return {"definition_version": DEFINITION_VERSION, "fields": field_catalog(editable_fields=permitted),
                "participant_roles": PARTICIPANT_ROLES}


@router.get("/directory/opportunity-members")
async def directory(
    q: str | None = Query(None, max_length=100), kind: Literal["all", "fde"] = "all",
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await member_directory(connection, query=q, kind=kind, limit=limit, offset=offset)


@router.get("/opportunities/{opportunity_id}/fields")
async def detail(
    opportunity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            return await read_fields(connection, identity.actor, str(opportunity_id))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.get("/opportunities/{opportunity_id}/intent-product-line-history", response_model=OpportunityIntentHistory)
async def intent_history(
    opportunity_id: UUID,
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    expected_history_revision: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    if offset and expected_history_revision is None:
        raise HTTPException(422, "继续翻页须携带首屏history_revision，请重新读取第一页")
    try:
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await read_intent_history(connection, identity.actor, opportunity_id, limit=limit, offset=offset,
                                             expected_history_revision=expected_history_revision)
    except VersionConflict as exc:
        raise HTTPException(409, {"code": "OPPORTUNITY_INTENT_HISTORY_CHANGED", "message": str(exc)}) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


async def save(database, actor, opportunity_id, body, key, *, people=False):
    if key is None:
        raise HTTPException(422, "商机字段维护必须携带Idempotency-Key")
    data = body.model_dump(mode="json", exclude_unset=True)
    values = {key: value for key, value in data.items() if key not in {"version_no", "reason"}} if people else data["values"]

    async def apply(connection):
        result = await patch_fields(connection, actor, opportunity_id, version_no=body.version_no,
                                    reason=body.reason, values=values, people_only=people)
        return {**await read_fields(connection, actor, opportunity_id), **result}

    async def authorize(connection):
        # Replays must recheck current object access before returning old data.
        await require_permission(connection, "opportunity.read", opportunity_id=opportunity_id)
        await require_permission(connection, "opportunity.update", opportunity_id=opportunity_id)
        if people and ("primary_fde_id" in values or values.get("fde_member_ids") is not None):
            await require_permission(connection, "opportunity.fde_members", opportunity_id=opportunity_id)

    return await management_write(database, actor, key, f"opportunity.{'people' if people else 'fields'}:{opportunity_id}",
                                  data, apply, authorize=authorize)


@router.patch("/opportunities/{opportunity_id}/fields")
async def update_fields(
    opportunity_id: UUID, body: OpportunityFieldPatch, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    return await save(database, identity.actor, str(opportunity_id), body, idempotency_key)


@router.put("/opportunities/{opportunity_id}/people")
async def update_people(
    opportunity_id: UUID, body: OpportunityPeoplePatch, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    return await save(database, identity.actor, str(opportunity_id), body, idempotency_key, people=True)
