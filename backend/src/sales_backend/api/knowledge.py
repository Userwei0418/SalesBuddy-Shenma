"""Mount this router through Backend-owned startup/permission wiring."""

from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile
from pydantic import Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.file_ranges import FILE_RANGE_RESPONSES, authorized_file_response
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.knowledge import (
    Code,
    ContentCategory,
    ContentScope,
    KnowledgeCatalog,
    KnowledgeContribution,
    KnowledgeContributionInput,
    KnowledgeCount,
    KnowledgeEnumOption,
    KnowledgeId,
    KnowledgeMaterialVersion,
    KnowledgeModel,
    VersionState,
)
from sales_backend.contracts.knowledge_import import (
    KnowledgeImportedContribution,
    KnowledgeImportInput,
    KnowledgeImportResult,
    KnowledgeImportSource,
)
from sales_backend.contracts.knowledge_ingestion import KnowledgeIngestionInput, KnowledgeIngestionView
from sales_backend.contracts.knowledge_retrieval import (
    KnowledgeAskInput,
    KnowledgeAskResponse,
    KnowledgeGenerateInput,
    KnowledgeGenerateReceipt,
    KnowledgeProviderBindingInput,
    KnowledgeProviderBindingReceipt,
    KnowledgePublishInput,
    KnowledgeWorkCandidatePage,
    KnowledgeWorkPage,
    KnowledgeWorkView,
)
from sales_backend.db import Database
from sales_backend.domain.knowledge import KnowledgeRuleError
from sales_backend.domain.knowledge_access import KnowledgeACL, KnowledgeACLGrant
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.knowledge import MAX_UPLOAD_BYTES, KnowledgeService
from sales_backend.services.knowledge_import import KnowledgeImportService
from sales_backend.services.knowledge_provider import KnowledgeProviderError


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"], dependencies=[Depends(no_cache)])
Revision = Annotated[int, Field(strict=True, ge=1)]
Options = Annotated[tuple[KnowledgeEnumOption, ...], Field(max_length=1000)]


class CatalogWrite(KnowledgeModel):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    general_product_code: Code
    products: Options
    industries: Options = ()
    reporting_positions: Options = ()
    reporting_levels: Options = ()
    decision_roles: Options = ()

    @model_validator(mode="after")
    def catalog_rules(self):
        # Validate only the client-owned values; the actual tenant is always server-resolved.
        KnowledgeCatalog(company_id=UUID(int=1), revision=1, **self.model_dump(exclude={"expected_revision"}))
        return self


class VersionWrite(KnowledgeModel):
    expected_material_revision: Revision
    contribution: KnowledgeContributionInput


class ACLWrite(KnowledgeModel):
    expected_acl_revision: Revision
    grants: Annotated[tuple[KnowledgeACLGrant, ...], Field(max_length=200)] = ()
    denied_user_ids: Annotated[tuple[KnowledgeId, ...], Field(max_length=200)] = ()

    @model_validator(mode="after")
    def unique_subjects(self):
        subjects = [(g.subject_kind, g.subject_id) for g in self.grants]
        if len(set(subjects)) != len(subjects) or len(set(self.denied_user_ids)) != len(self.denied_user_ids):
            raise ValueError("KNOWLEDGE_ACL_DUPLICATE_SUBJECT")
        return self


class FileView(KnowledgeModel):
    file_id: KnowledgeId
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    # The detail endpoint uses this to decide whether the browser can offer
    # preview/new-tab browsing.  It is optional for the upload receipt so old
    # callers remain compatible; material detail fills it from the file bytes.
    preview_supported: bool = False


class SaveReceipt(KnowledgeModel):
    material_id: KnowledgeId
    version_id: KnowledgeId
    version_no: Revision
    material_revision: Revision
    content_name: str
    state: VersionState
    active_version_id: KnowledgeId | None
    uploaded_at: datetime
    contributor_user_id: KnowledgeId


class MaterialView(KnowledgeModel):
    material_id: KnowledgeId
    content_name: str
    material_revision: Revision
    latest_version_id: KnowledgeId
    active_version_id: KnowledgeId | None
    updated_at: datetime
    active_version_state: VersionState | None
    state: VersionState
    acl_revision: Revision
    can_edit: bool
    can_manage: bool
    version_id: KnowledgeId
    version_no: Revision
    created_at: datetime
    uploaded_at: datetime
    content_category: ContentCategory
    content_scope: ContentScope
    product_code: Code
    product_label: str | None
    industry_code: Code | None
    industry_label: str | None
    customer_id: KnowledgeId | None
    customer_name: str | None
    opportunity_name: str | None
    contributor_user_id: KnowledgeId
    contributor_name: str | None
    sharing_label: str
    contributor_code: str
    contributor_label: str
    source: KnowledgeImportSource | None = None


class DirectoryProductOption(KnowledgeModel):
    code: Code
    label: str


class MaterialPage(KnowledgeModel):
    items: tuple[MaterialView, ...]
    total: int
    limit: int
    offset: int
    scope_label: str
    capabilities: dict[Literal["create"], bool]
    product_options: tuple[DirectoryProductOption, ...] = ()


class DirectoryFilters(KnowledgeModel):
    q: Annotated[str, Field(max_length=200)] = ""
    content_category: ContentCategory | None = None
    product_code: Code | None = None
    state: VersionState | None = None
    scope: Literal["all", "personal", "team", "shared"] = "all"
    period: Literal["all", "30d"] = "all"
    retrievable: bool | None = None
    external: bool | None = None
    mine: bool | None = None
    contributor_code: Annotated[str, Field(max_length=2100)] | None = None


class DirectoryQuery(DirectoryFilters):
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100000)] = 0


class StatisticBucket(KnowledgeModel):
    code: str
    label: str
    count: int


class ContributionBucket(KnowledgeModel):
    code: str
    label: str
    contributor_user_id: KnowledgeId | None
    contributor_name: str | None
    count: int


class TrendPoint(KnowledgeModel):
    date: str
    count: int
    external_count: int


class ProcessingAlert(KnowledgeModel):
    code: VersionState
    state: VersionState
    count: int
    message: str


class KnowledgeStats(KnowledgeModel):
    source: Literal["salesbuddy.internal_materials"]
    observed_at: datetime
    scope_label: str
    time_zone: Literal["UTC", "Asia/Shanghai"]
    filters: DirectoryFilters
    metrics: dict[
        Literal["total", "added_30d", "retrievable", "failed", "external", "contributors", "mine"], KnowledgeCount
    ]
    categories: tuple[StatisticBucket, ...]
    products: tuple[StatisticBucket, ...]
    contributions: tuple[ContributionBucket, ...]
    trend: tuple[TrendPoint, ...]
    alerts: tuple[ProcessingAlert, ...]


class VersionView(KnowledgeMaterialVersion):
    contribution: KnowledgeContribution | KnowledgeImportedContribution
    files: tuple[FileView, ...]


class MaterialDetail(MaterialView):
    versions: tuple[VersionView, ...]


class ACLReceipt(KnowledgeModel):
    material_id: KnowledgeId
    acl_revision: Revision


class ProviderBindingReceipt(KnowledgeProviderBindingReceipt):
    observed_at: datetime


class ACLCandidate(KnowledgeModel):
    subject_id: KnowledgeId
    display_name: str


class ACLCandidatePage(KnowledgeModel):
    material_id: KnowledgeId
    subject_kind: Literal["user", "team"]
    items: tuple[ACLCandidate, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100000)


def service(database: Database = Depends(get_database), settings=Depends(get_settings)):
    return KnowledgeService(database, settings)


@contextmanager
def errors():
    try:
        yield
    except IdempotencyConflict as exc:
        raise HTTPException(409, "IDEMPOTENCY_CONFLICT") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        code = str(exc).strip("'")
        raise HTTPException(409 if code == "KNOWLEDGE_CATALOG_UNCONFIGURED" else 404, code) from exc
    except KnowledgeRuleError as exc:
        status = 409 if exc.code in {"KNOWLEDGE_CATALOG_CHANGED", "KNOWLEDGE_NAME_CONFLICT"} else 422
        raise HTTPException(status, exc.code) from exc
    except KnowledgeProviderError as exc:
        status = (
            409
            if exc.code
            in {
                "KNOWLEDGE_PROVIDER_UNCONFIGURED",
                "KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED",
                "KNOWLEDGE_PROVIDER_BINDING_MISSING",
                "KNOWLEDGE_INGESTION_CONFIGURATION_CHANGED",
                "KNOWLEDGE_WORK_PUBLISH_RECIPIENTS_REQUIRED",
                "KNOWLEDGE_GENERATION_NO_EVIDENCE",
            }
            else 503
        )
        if exc.code in {
            "KNOWLEDGE_PROVIDER_FORMAT_UNSUPPORTED",
            "KNOWLEDGE_DOCUMENT_SOURCE_LIMIT",
            "KNOWLEDGE_DOCUMENT_INPUT_INVALID",
        }:
            status = 422
        raise HTTPException(status, exc.code) from exc
    except ValueError as exc:
        code = str(exc)
        if code == "KNOWLEDGE_FILE_TOO_LARGE":
            raise HTTPException(413, code) from exc
        allowed = {
            "KNOWLEDGE_FILE_SIZE_INVALID",
            "KNOWLEDGE_IMPORT_NOT_RETRIEVABLE",
            "KNOWLEDGE_INGESTION_FILES_REQUIRED",
            "KNOWLEDGE_INGESTION_LINKS_NOT_ARCHIVED",
        }
        raise HTTPException(422, code if code in allowed else "KNOWLEDGE_INPUT_INVALID") from exc
    except asyncpg.PostgresError as exc:
        statuses = {
            "42501": 403,
            "P0002": 404,
            "40001": 409,
            "22023": 422,
            "22P02": 422,
            "23514": 422,
            "55000": 409,
            "P0413": 413,
        }
        if exc.sqlstate not in statuses:
            raise
        # Never expose relation/SQL/internal detail from the driver.
        message = exc.message if exc.message.startswith("KNOWLEDGE_") else "KNOWLEDGE_REQUEST_REJECTED"
        raise HTTPException(statuses[exc.sqlstate], message) from exc


@router.get("/catalog", response_model=KnowledgeCatalog)
async def catalog(identity: RequestIdentity = Depends(get_identity), svc: KnowledgeService = Depends(service)):
    with errors():
        return await svc.read(identity.actor, "catalog")


@router.put("/catalog", response_model=KnowledgeCatalog)
async def put_catalog(
    body: CatalogWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.save_catalog(identity.actor, idempotency_key, body)


@router.post(
    "/files",
    response_model=FileView,
    status_code=201,
    responses={413: {"description": "KNOWLEDGE_FILE_TOO_LARGE: file exceeds 50 MiB (52428800 bytes)"}},
)
async def upload(
    file: UploadFile = File(..., description="One archived file, 1 to 52428800 bytes (50 MiB)."),
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "KNOWLEDGE_FILE_TOO_LARGE")
        with errors():
            return await svc.upload(identity.actor, idempotency_key, file.filename, file.content_type, content)
    finally:
        await file.close()


@router.get("/materials", response_model=MaterialPage)
async def materials(
    query: Annotated[DirectoryQuery, Query()],
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.read(
            identity.actor, "list_materials", query.limit, query.offset, query.model_dump(exclude={"limit", "offset"})
        )


@router.post("/imports/feishu", response_model=KnowledgeImportResult)
async def import_feishu(
    body: KnowledgeImportInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    with errors():
        return await KnowledgeImportService(database).run(identity.actor, idempotency_key, body)


@router.get("/stats", response_model=KnowledgeStats)
async def stats(
    query: Annotated[DirectoryFilters, Query()],
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "stats", query.model_dump())


@router.get("/materials/{material_id}", response_model=MaterialDetail)
async def material(
    material_id: UUID, identity: RequestIdentity = Depends(get_identity), svc: KnowledgeService = Depends(service)
):
    with errors():
        return await svc.read(identity.actor, "detail", material_id)


@router.post("/materials", response_model=SaveReceipt, status_code=201)
async def create(
    body: KnowledgeContributionInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.save(identity.actor, idempotency_key, body)


@router.post("/materials/{material_id}/versions", response_model=SaveReceipt, status_code=201)
async def version(
    material_id: UUID,
    body: VersionWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.save(
            identity.actor, idempotency_key, body.contribution, material_id, body.expected_material_revision
        )


@router.get("/materials/{material_id}/versions/{version_id}/ingestion", response_model=KnowledgeIngestionView)
async def ingestion(
    material_id: UUID,
    version_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.ingestion.view(identity.actor, material_id, version_id)


@router.post(
    "/materials/{material_id}/versions/{version_id}/ingestion", response_model=KnowledgeIngestionView, status_code=202
)
async def start_ingestion(
    material_id: UUID,
    version_id: UUID,
    body: KnowledgeIngestionInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.ingestion.start(identity.actor, idempotency_key, material_id, version_id, body)


@router.get("/materials/{material_id}/acl", response_model=KnowledgeACL)
async def acl(
    material_id: UUID, identity: RequestIdentity = Depends(get_identity), svc: KnowledgeService = Depends(service)
):
    with errors():
        return await svc.read(identity.actor, "acl", material_id)


@router.get("/materials/{material_id}/acl-candidates", response_model=ACLCandidatePage)
async def acl_candidates(
    material_id: UUID,
    subject_kind: Literal["user", "team"],
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "acl_candidates", material_id, subject_kind, q.strip(), limit, offset)


@router.put("/materials/{material_id}/acl", response_model=ACLReceipt)
async def put_acl(
    material_id: UUID,
    body: ACLWrite,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.save_acl(identity.actor, idempotency_key, material_id, body)


@router.put("/materials/{material_id}/versions/{version_id}/provider-binding", response_model=ProviderBindingReceipt)
async def put_provider_binding(
    material_id: UUID,
    version_id: UUID,
    body: KnowledgeProviderBindingInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.bind_provider(identity.actor, idempotency_key, material_id, version_id, body)


@router.get(
    "/materials/{material_id}/versions/{version_id}/provider-binding",
    response_model=ProviderBindingReceipt | None,
)
async def provider_binding(
    material_id: UUID,
    version_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.provider_binding(identity.actor, material_id, version_id)


def file_response(row, *, preview=False, ranges=False, range_header=None, if_match=None):
    disposition = "attachment"
    media_type = "application/octet-stream"
    if preview:
        # Serve controlled bytes to the local renderer. A PDF signature is not
        # a claim that the complete document is valid or safely parsed here.
        if row["media_type"] == "application/pdf" and row["content"].startswith(b"%PDF-"):
            media_type = "application/pdf"
        elif row["media_type"] in {"text/plain", "text/markdown", "text/csv"}:
            try:
                row["content"].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(415, "KNOWLEDGE_PREVIEW_UNSUPPORTED") from exc
            media_type = "text/plain"
        elif row["media_type"] == "image/png" and row["content"].startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif row["media_type"] == "image/jpeg" and row["content"].startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        elif (
            row["media_type"] == "image/webp"
            and len(row["content"]) >= 12
            and row["content"][:4] == b"RIFF"
            and row["content"][8:12] == b"WEBP"
        ):
            media_type = "image/webp"
        else:
            raise HTTPException(415, "KNOWLEDGE_PREVIEW_UNSUPPORTED")
        disposition = "inline"
    respond = authorized_file_response if ranges else Response
    return respond(
        row["content"],
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(row['filename'], safe='')}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Referrer-Policy": "no-referrer",
        },
        **({"range_header": range_header, "if_match": if_match} if ranges else {}),
    )


@router.get("/files/{file_id}/download", response_class=Response, responses=FILE_RANGE_RESPONSES)
async def download(
    file_id: UUID,
    version_id: UUID,
    range_header: str | None = Header(default=None, alias="Range", max_length=200),
    if_match: str | None = Header(default=None, alias="If-Match", max_length=200),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return file_response(
            await svc.download(identity.actor, file_id, version_id),
            ranges=True,
            range_header=range_header,
            if_match=if_match,
        )


@router.get(
    "/files/{file_id}/preview",
    response_class=Response,
    responses={
        **FILE_RANGE_RESPONSES,
        200: {
            "description": "Current version-authorized bytes for local rendering; no static URL.",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "text/plain": {"schema": {"type": "string"}},
            },
        },
        415: {
            "description": "KNOWLEDGE_PREVIEW_UNSUPPORTED: unsupported format, invalid PDF signature or text encoding"
        },
    },
)
async def preview(
    file_id: UUID,
    version_id: UUID,
    range_header: str | None = Header(default=None, alias="Range", max_length=200),
    if_match: str | None = Header(default=None, alias="If-Match", max_length=200),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return file_response(
            await svc.download(identity.actor, file_id, version_id),
            preview=True,
            ranges=True,
            range_header=range_header,
            if_match=if_match,
        )


@router.get("/count", response_model=KnowledgeCount)
async def count(identity: RequestIdentity = Depends(get_identity), svc: KnowledgeService = Depends(service)):
    with errors():
        return await svc.count(identity.actor)


@router.get("/provider/status")
async def provider_status(identity: RequestIdentity = Depends(get_identity), svc: KnowledgeService = Depends(service)):
    """Return provider capability state without exposing credentials or tenant data."""
    with errors():
        return await svc.provider_status(identity.actor)


@router.post("/ask", response_model=KnowledgeAskResponse)
async def ask(
    body: KnowledgeAskInput,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.ask(identity.actor, body)


@router.get("/works", response_model=KnowledgeWorkPage)
async def works(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.list_works(identity.actor, limit=limit, offset=offset)


@router.post("/works", response_model=KnowledgeGenerateReceipt, status_code=202)
async def generate_work(
    body: KnowledgeGenerateInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.generate_work(identity.actor, idempotency_key, body)


@router.get("/works/{work_id}", response_model=KnowledgeWorkView)
async def work(
    work_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.read_work(identity.actor, work_id)


@router.post("/works/{work_id}/publish", response_model=KnowledgeWorkView)
async def publish_work(
    work_id: UUID,
    body: KnowledgePublishInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.publish_work(identity.actor, idempotency_key, work_id, body)


@router.get("/works/{work_id}/share-candidates", response_model=KnowledgeWorkCandidatePage)
async def work_candidates(
    work_id: UUID,
    subject_kind: Literal["user", "team"],
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return await svc.read(identity.actor, "work_candidates", work_id, subject_kind, q.strip(), limit, offset)


@router.get("/works/{work_id}/files/{file_id}/download", response_class=Response, responses=FILE_RANGE_RESPONSES)
async def work_download(
    work_id: UUID,
    file_id: UUID,
    range_header: str | None = Header(default=None, alias="Range", max_length=200),
    if_match: str | None = Header(default=None, alias="If-Match", max_length=200),
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeService = Depends(service),
):
    with errors():
        return file_response(
            await svc.download_work(identity.actor, work_id, file_id),
            ranges=True,
            range_header=range_header,
            if_match=if_match,
        )
