"""Opaque customer links, without internal sessions or bearer tokens in URLs."""

from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import AwareDatetime

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.knowledge import file_response
from sales_backend.contracts.knowledge import ContentCategory
from sales_backend.contracts.knowledge_distribution import (
    AnalyticsQuery,
    AnswerPage,
    BrowserId,
    DistributionPage,
    GrantView,
    PublicationView,
    PublicationWrite,
    PublicShare,
    ResponseWrite,
    RevokeWrite,
    SharePage,
    ShareView,
    ShareWrite,
    StatisticView,
    Token,
)
from sales_backend.db import Database
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.knowledge_distribution import DistributionService


def private_response(response: Response):
    response.headers.update(
        {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
    )


router = APIRouter(
    prefix="/api/v1/knowledge/distribution", tags=["knowledge-distribution"], dependencies=[Depends(private_response)]
)
public_router = APIRouter(
    prefix="/api/v1/public/knowledge", tags=["knowledge-public"], dependencies=[Depends(private_response)]
)
ShareToken = Annotated[Token, Header(alias="X-Knowledge-Share")]
GrantToken = Annotated[Token | None, Header(alias="X-Knowledge-Grant")]


def service(database: Database = Depends(get_database)):
    return DistributionService(database)


@contextmanager
def errors():
    try:
        yield
    except IdempotencyConflict as exc:
        raise HTTPException(409, "KNOWLEDGE_IDEMPOTENCY_CONFLICT") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "KNOWLEDGE_INPUT_INVALID") from exc
    except asyncpg.PostgresError as exc:
        statuses = {
            "42501": 403,
            "P0002": 404,
            "40001": 409,
            "22023": 422,
            "22P02": 422,
            "23514": 422,
            "P0415": 415,
            "P0429": 429,
        }
        if exc.sqlstate not in statuses:
            raise
        raise HTTPException(
            statuses[exc.sqlstate],
            exc.message if exc.message.startswith("KNOWLEDGE_") else "KNOWLEDGE_REQUEST_REJECTED",
        ) from exc


@router.get("/materials", response_model=DistributionPage)
async def materials(
    q: str = Query(default="", max_length=200),
    content_category: ContentCategory | None = None,
    product_code: str | None = Query(default=None, max_length=100),
    date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        if date_from and date_to and date_from >= date_to:
            raise HTTPException(422, "KNOWLEDGE_INPUT_INVALID")
        return await svc.read(
            identity.actor, "materials", q, content_category, product_code, limit, offset, date_from, date_to
        )


@router.get("/materials/{material_id}/publication", response_model=PublicationView)
async def publication(
    material_id: UUID, identity: RequestIdentity = Depends(get_identity), svc: DistributionService = Depends(service)
):
    with errors():
        return await svc.read(identity.actor, "publication", material_id)


@router.put("/materials/{material_id}/publication", response_model=PublicationView)
async def publish(
    material_id: UUID,
    body: PublicationWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.publish(identity.actor, idempotency_key, material_id, body)


@router.post("/materials/{material_id}/shares", response_model=ShareView, status_code=201)
async def create_share(
    material_id: UUID,
    body: ShareWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.share(identity.actor, idempotency_key, material_id, body)


@router.get("/shares", response_model=SharePage)
async def shares(
    query: Annotated[AnalyticsQuery, Query()],
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "shares", query)


@router.post("/shares/{share_id}/revoke", response_model=ShareView)
async def revoke_share(
    share_id: UUID,
    body: RevokeWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.revoke(identity.actor, idempotency_key, share_id, body)


@router.get("/statistics", response_model=StatisticView)
async def statistics(
    query: Annotated[AnalyticsQuery, Query()],
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "statistics", query)


@router.get("/responses", response_model=AnswerPage)
async def responses(
    query: Annotated[AnalyticsQuery, Query()],
    identity: RequestIdentity = Depends(get_identity),
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "responses", query)


@public_router.get("/share", response_model=PublicShare)
async def public_share(share_token: ShareToken, browser_id: BrowserId, svc: DistributionService = Depends(service)):
    with errors():
        return await svc.public("metadata", share_token, browser_id=browser_id)


@public_router.post("/responses", response_model=GrantView)
async def public_response(
    body: ResponseWrite,
    share_token: ShareToken,
    idempotency_key: MutationKey = None,
    svc: DistributionService = Depends(service),
):
    with errors():
        return await svc.public("response", share_token, body=body, key=idempotency_key)


@public_router.get(
    "/files/{file_id}/preview",
    response_class=Response,
    responses={
        200: {
            "description": "Questionnaire-granted controlled bytes; PDF signature checked; no static URL.",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        },
        403: {"description": "Questionnaire grant required or publication preview forbidden"},
        404: {"description": "Share, publication, current access or bound file unavailable"},
        415: {
            "description": "KNOWLEDGE_PREVIEW_UNSUPPORTED: unsupported format, invalid PDF signature or text encoding"
        },
    },
)
async def public_preview(
    file_id: UUID, share_token: ShareToken, grant_token: GrantToken = None, svc: DistributionService = Depends(service)
):
    with errors():
        return file_response(await svc.public("preview", share_token, grant=grant_token, file_id=file_id), preview=True)


@public_router.get("/files/{file_id}/download", response_class=Response)
async def public_download(
    file_id: UUID, share_token: ShareToken, grant_token: GrantToken = None, svc: DistributionService = Depends(service)
):
    with errors():
        return file_response(await svc.public("download", share_token, grant=grant_token, file_id=file_id))


@public_router.get("/cover", response_class=Response)
async def public_cover(share_token: ShareToken, svc: DistributionService = Depends(service)):
    with errors():
        row = await svc.public("cover", share_token)
        return Response(
            row["content"],
            media_type=row["media_type"],
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )
