"""A separate read surface. No ordinary role, service fallback or side effects."""
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from sales_backend.api.dependencies import bearer, get_database, get_settings
from sales_backend.auth.models import RestrictedSessionInfo
from sales_backend.auth.restricted import RestrictedIdentity, RestrictedTokens
from sales_backend.auth.tokens import TokenError
from sales_backend.services.restricted import RestrictedService

router = APIRouter(prefix="/api/v1/demo-view", tags=["Mini read-only business"])


class Reference(BaseModel):
    id: str
    name: str


class CustomerView(BaseModel):
    id: str
    name: str
    industry: str | None = None
    level: str | None = None
    stage: str | None = None
    description: str | None = None
    updated_at: datetime | None = None


class OpportunityView(BaseModel):
    id: str
    name: str
    customer: Reference | None = None
    product: str | None = None
    stage: str | None = None
    amount: Decimal | None = None
    expected_close_date: date | None = None
    updated_at: datetime | None = None


class FollowUpView(BaseModel):
    id: str
    customer: Reference | None = None
    opportunity: Reference | None = None
    follow_up_date: date | None = None
    type: str | None = None
    display_text: str | None = None
    updated_at: datetime | None = None


class TaskView(BaseModel):
    id: str
    title: str
    status: str
    due_at: datetime | None = None
    execution_mode: Literal["legacy", "execution_v1", "execution_v2", "execution_v3"] = "legacy"
    processing_reason: Literal["adjustment_request", "paused", "awaiting_review"] | None = None
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    description: str | None = None
    customer: Reference | None = None
    opportunity: Reference | None = None
    updated_at: datetime | None = None


    @model_validator(mode="after")
    def lightweight_progress(self):
        if self.execution_mode == "execution_v3":
            self.progress_percent = None
        return self


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    data_version: str
    updated_at: datetime | None = None


class Overview(BaseModel):
    customer_count: int = Field(ge=0)
    opportunity_count: int = Field(ge=0)
    follow_up_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    data_version: str
    updated_at: datetime | None = None


def service(request: Request):
    return RestrictedService(get_database(request), get_settings(request))


async def restricted_identity(request: Request, response: Response) -> RestrictedIdentity:
    credentials = await bearer(request)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, {"code": "RESTRICTED_SESSION_EXPIRED"})
    try:
        identity = RestrictedTokens(get_settings(request)).decode(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(401, {"code": "RESTRICTED_SESSION_EXPIRED"}) from exc
    if request.headers.get("X-Company-ID", identity.workspace_id) != identity.workspace_id:
        raise HTTPException(403, {"code": "RESTRICTED_ENDPOINT_DENIED"})
    response.headers["Cache-Control"] = "no-store"
    instance = service(request)
    async with instance.database.connection() as connection:
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL statement_timeout = '5s'")
            await connection.execute("SET LOCAL lock_timeout = '1s'")
            info = await instance.validate(connection, identity)
    # Do not bind this identity as RequestIdentity/current_actor: it has no role.
    request.state.restricted_session = info
    await instance.admit(identity)
    return identity


@router.get("/session", response_model=RestrictedSessionInfo)
async def session(request: Request, identity: RestrictedIdentity = Depends(restricted_identity)):
    return request.state.restricted_session


@router.post("/logout", status_code=204)
async def logout(request: Request):
    credentials = await bearer(request)
    if credentials is None:
        raise HTTPException(401, {"code": "RESTRICTED_SESSION_EXPIRED"})
    try:
        # A valid expired proof can only revoke itself, and a repeated logout
        # succeeds; it never restores a session or grants read authority.
        identity = RestrictedTokens(get_settings(request)).decode(credentials.credentials, verify_exp=False)
    except TokenError as exc:
        raise HTTPException(401, {"code": "RESTRICTED_SESSION_EXPIRED"}) from exc
    instance = service(request)
    async with instance.database.connection() as connection:
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout = '5s'")
            await connection.execute("SET LOCAL lock_timeout = '1s'")
            await instance.repository.logout(connection, identity)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/overview", response_model=Overview)
async def overview(request: Request, identity: RestrictedIdentity = Depends(restricted_identity)):
    return await service(request).read(identity, "overview")


async def read_list(request, identity, resource, q, limit, offset):
    return await service(request).read(identity, resource, query=q, limit=limit, offset=offset)


@router.get("/customers", response_model=Page[CustomerView])
async def customers(request: Request, identity: RestrictedIdentity = Depends(restricted_identity),
                    q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50),
                    offset: int = Query(0, ge=0, le=10000)):
    return await read_list(request, identity, "customers", q, limit, offset)


@router.get("/customers/{record_id}", response_model=CustomerView)
async def customer(request: Request, record_id: UUID, identity: RestrictedIdentity = Depends(restricted_identity)):
    return await service(request).read(identity, "customers", str(record_id))


@router.get("/opportunities", response_model=Page[OpportunityView])
async def opportunities(request: Request, identity: RestrictedIdentity = Depends(restricted_identity),
                        q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50),
                        offset: int = Query(0, ge=0, le=10000)):
    return await read_list(request, identity, "opportunities", q, limit, offset)


@router.get("/opportunities/{record_id}", response_model=OpportunityView)
async def opportunity(request: Request, record_id: UUID, identity: RestrictedIdentity = Depends(restricted_identity)):
    return await service(request).read(identity, "opportunities", str(record_id))


@router.get("/follow-ups", response_model=Page[FollowUpView])
async def follow_ups(request: Request, identity: RestrictedIdentity = Depends(restricted_identity),
                     q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50),
                     offset: int = Query(0, ge=0, le=10000)):
    return await read_list(request, identity, "follow-ups", q, limit, offset)


@router.get("/follow-ups/{record_id}", response_model=FollowUpView)
async def follow_up(request: Request, record_id: UUID, identity: RestrictedIdentity = Depends(restricted_identity)):
    return await service(request).read(identity, "follow-ups", str(record_id))


@router.get("/tasks", response_model=Page[TaskView])
async def tasks(request: Request, identity: RestrictedIdentity = Depends(restricted_identity),
                 q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50),
                 offset: int = Query(0, ge=0, le=10000)):
    return await read_list(request, identity, "tasks", q, limit, offset)


@router.get("/tasks/{record_id}", response_model=TaskView)
async def task(request: Request, record_id: UUID, identity: RestrictedIdentity = Depends(restricted_identity)):
    return await service(request).read(identity, "tasks", str(record_id))
