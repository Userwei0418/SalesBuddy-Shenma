"""Authenticated private playback; no public object URL or bearer query parameter."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.file_ranges import FILE_RANGE_RESPONSES, authorized_file_response
from sales_backend.api.live_identity import live_identity_connection
from sales_backend.contracts.types import UUIDString
from sales_backend.db import Database
from sales_backend.repositories.visit_audio import audio_metadata, own_audio_location
from sales_backend.services.material_storage import MaterialStorage

router = APIRouter(prefix="/api/v1/visits", tags=["Visit audio"])


@router.get("/{visit_id}/audio", response_class=Response, responses=FILE_RANGE_RESPONSES,
            description="仅当前公司原录音上传人且为原跟进人可读取；每次按当前跟进可见权限鉴权，支持单段 Range。")
async def playback(
    visit_id: UUIDString, request: Request,
    range_header: str | None = Header(default=None, alias="Range", max_length=200),
    if_match: str | None = Header(default=None, alias="If-Match", max_length=200),
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await own_audio_location(connection, visit_id)
    if not row:
        raise HTTPException(404, "录音不存在或当前不可回听")
    try:
        content = await MaterialStorage().read(database, identity.actor, row)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "录音暂不可读取，请稍后重试") from exc
    async with live_identity_connection(request, identity, database) as (connection, _):
        latest = await own_audio_location(connection, visit_id)
        if latest is None:
            raise HTTPException(404, "录音不存在或当前不可回听")
        if latest != row:
            raise HTTPException(409, "录音已变化，请重新读取")
    return authorized_file_response(
        content, media_type=audio_metadata(row)["mime_type"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": "inline"},
        range_header=range_header, if_match=if_match,
    )
