from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.db import Database
from sales_backend.repositories.visit_imports import import_detail, original_location
from sales_backend.repositories.visit_imports import retry_import as retry_import_record
from sales_backend.services.capabilities import require_capability
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.visit_import import IMPORT_ROOT
from sales_backend.services.visit_upload import LocalVisitImportStorage, UploadRejected, VisitUploadService

router = APIRouter(prefix="/api/v1/visit-imports", tags=["Visit imports"])


@router.post("", status_code=202)
async def upload_visit_file(
    file: UploadFile = File(...),
    original_filename: str = Form(default=""),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
) -> dict:
    service = VisitUploadService(database, LocalVisitImportStorage(IMPORT_ROOT), registry=MaterialStorage())
    try:
        return await service.upload(identity.actor, source=file, filename=original_filename or file.filename or "")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except UploadRejected as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


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
    import_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
) -> dict:
    try:
        async with database.transaction(identity.actor) as connection:
            await require_capability(connection, identity.actor, "visit.create")
            return await retry_import_record(connection, identity.actor, import_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{import_id}/original", response_class=Response)
async def download_original(
    import_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await original_location(connection, identity.actor, import_id)
    if not row:
        raise HTTPException(404, "原始材料不存在或无权查看")
    try:
        content = await MaterialStorage().read(database, identity.actor, row)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "原始材料暂不可读取，请联系运营") from exc
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"], safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
