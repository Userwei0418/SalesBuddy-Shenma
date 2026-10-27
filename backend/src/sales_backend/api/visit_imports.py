from urllib.parse import quote
from typing import Literal
from uuid import UUID
import asyncpg

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.responses import JSONResponse

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.live_identity import live_identity_connection
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.upload_admission import VisitUploadRoute
from sales_backend.db import Database
from sales_backend.contracts.visit_upload import LegacyUploadReceipt, UploadRegistered, UploadPending, UploadError
from sales_backend.repositories.visit_imports import import_detail, original_location
from sales_backend.repositories.visit_imports import retry_import as retry_import_record
from sales_backend.repositories.visit_imports import require_retry_access
from sales_backend.repositories import visit_upload_intents as intents
from sales_backend.services.capabilities import require_capability
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.visit_import import IMPORT_ROOT
from sales_backend.services.visit_upload import LocalVisitImportStorage, UploadRejected, VisitUploadService
from sales_backend.services.idempotency import execute_mutation
from sales_backend.error_contract import error_response

router = APIRouter(prefix="/api/v1/visit-imports", tags=["Visit imports"])


async def upload_visit_file(
    file: UploadFile = File(...),
    original_filename: str = Form(default=""),
    source_kind: Literal["recording", "file"] | None = Form(default=None),
    asr_session_id: UUID | None = Form(default=None),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    service = VisitUploadService(database, LocalVisitImportStorage(IMPORT_ROOT), registry=MaterialStorage())
    try:
        return await service.upload(identity.actor, source=file, filename=original_filename or file.filename or "",
                                    intent_key=idempotency_key, source_kind=source_kind or "legacy",
                                    asr_session_id=str(asr_session_id) if asr_session_id else None)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except UploadRejected as exc:
        return error_response(status_code=exc.status_code, detail=str(exc), code=exc.code)
    except intents.UploadIntentConflict as exc:
        return error_response(status_code=409, detail=str(exc), code="UPLOAD_INTENT_CONFLICT")
    except intents.UploadStatusUnavailable as exc:
        return error_response(status_code=503, detail=str(exc), code="UPLOAD_STATUS_UNAVAILABLE")
    except (TimeoutError, ConnectionError, asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError):
        return error_response(status_code=503, detail="上传结果暂不可核实，请保留原文件并查回", code="UPLOAD_STATUS_UNAVAILABLE")


router.add_api_route("", upload_visit_file, methods=["POST"], status_code=202, route_class_override=VisitUploadRoute,
                    response_model=UploadRegistered | UploadPending | LegacyUploadReceipt,
                    responses={409: {"model": UploadError}, 503: {"model": UploadError}})


@router.get("/requests/{key}", response_model=UploadRegistered | UploadPending,
            responses={404: {"model": UploadError}, 503: {"model": UploadError}})
async def upload_request_status(
    key: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            await require_capability(connection, identity.actor, "visit.upload")
            row = await intents.lookup(connection, key)
            if row is None:
                return error_response(status_code=404, detail="未找到本次上传意图", code="UPLOAD_INTENT_NOT_FOUND",
                                      headers={"Cache-Control": "private, no-store"})
            result = intents.receipt(row)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (intents.UploadStatusUnavailable, TimeoutError, ConnectionError, asyncpg.PostgresConnectionError,
            asyncpg.CannotConnectNowError):
        return error_response(status_code=503, detail="上传结果暂不可核实，请保留原文件稍后查询", code="UPLOAD_STATUS_UNAVAILABLE",
                              headers={"Cache-Control": "private, no-store"})
    headers = {"Cache-Control": "private, no-store"}
    if result["state"] != "registered":
        headers["Retry-After"] = "2"
    return JSONResponse(result, headers=headers)


@router.get("/{import_id}")
async def import_status(
    import_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
) -> dict:
    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await import_detail(connection, identity.actor, import_id)
    if not row:
        raise HTTPException(404, "文件任务不存在")
    return dict(row)


@router.post("/{import_id}/retry", status_code=202)
async def retry_import(
    import_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "visit.retry_import")
            await require_retry_access(connection, identity.actor, import_id)
            await execute_mutation(connection, identity.actor, idempotency_key, f"visit_import.retry:{import_id}", {},
                                   lambda: retry_import_record(connection, identity.actor, import_id))
            # A replay retains the original retry effect but reports current
            # processing/failed status, even if the Worker failed again.
            current = await require_retry_access(connection, identity.actor, import_id)
            return {"id": str(import_id), "status": current["status"]}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{import_id}/original", response_class=Response)
async def download_original(
    import_id: UUID, request: Request,
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await original_location(connection, identity.actor, import_id)
    if not row:
        raise HTTPException(404, "原始材料不存在或无权查看")
    try:
        content = await MaterialStorage().read(database, identity.actor, row)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "原始材料暂不可读取，请联系运营") from exc
    # Object storage/local I/O may finish after logout, suspension or revocation.
    # Do not release its bytes based on the authorization from before that wait.
    async with live_identity_connection(request, identity, database) as (connection, _):
        latest = await original_location(connection, identity.actor, import_id)
        if not latest:
            raise PermissionError("原始材料已不可访问，请重新查询")
        if latest != row:
            raise HTTPException(409, "原始材料已变更，请重新下载")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"], safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
