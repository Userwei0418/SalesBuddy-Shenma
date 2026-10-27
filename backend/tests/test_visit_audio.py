from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from sales_backend.api import visit_audio
from sales_backend.repositories.visit_audio import audio_metadata
from sales_backend.services.closed_company_export import ClosedExportSnapshot
from sales_backend.services.tenant_export import ActiveExportSnapshot

VISIT = "10000000-0000-0000-0000-000000000001"
ROW = {"id": "20000000-0000-0000-0000-000000000001", "visit_id": VISIT,
       "filename": "我的录音.MP3", "file_size": 10, "storage_key": "private-location"}


class Database:
    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield object()


def prepare(monkeypatch, rows):
    location = AsyncMock(side_effect=rows)
    read = AsyncMock(return_value=b"0123456789")
    monkeypatch.setattr(visit_audio, "own_audio_location", location)
    monkeypatch.setattr(visit_audio, "MaterialStorage", lambda: SimpleNamespace(read=read))
    @asynccontextmanager
    async def live(*args):
        yield object(), object()
    monkeypatch.setattr(visit_audio, "live_identity_connection", live)
    return location, read


@pytest.mark.asyncio
async def test_owner_audio_has_exact_private_bytes_and_range(monkeypatch):
    location, read = prepare(monkeypatch, [ROW, ROW])
    response = await visit_audio.playback(VISIT, object(), "bytes=2-5", None,
                                         SimpleNamespace(actor=object()), Database())
    assert response.status_code == 206 and response.body == b"2345"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert location.await_count == 2 and read.await_count == 1
    assert audio_metadata(ROW) == {"id": ROW["id"], "filename": ROW["filename"], "size_bytes": 10,
                                   "mime_type": "audio/mpeg", "playback_path": f"/visits/{VISIT}/audio"}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,reads", [([None], 0), ([ROW, None], 1)])
async def test_denied_or_revoked_owner_gets_no_audio(monkeypatch, rows, reads):
    _, read = prepare(monkeypatch, rows)
    with pytest.raises(HTTPException) as raised:
        await visit_audio.playback(VISIT, object(), None, None, SimpleNamespace(actor=object()), Database())
    assert raised.value.status_code == 404
    assert read.await_count == reads


@pytest.mark.asyncio
async def test_active_and_closed_export_refuse_denied_audio_before_storage():
    for snapshot in (ActiveExportSnapshot(None, None, None, "now"),
                     ClosedExportSnapshot(None, "hash", None, True, None, "now")):
        with pytest.raises(PermissionError, match="仅支持回听"):
            await snapshot.read_original({**ROW, "original_download_allowed": False})


def test_audio_privacy_formats_cover_every_accepted_import_format():
    from sales_backend.repositories.visit_audio import AUDIO_MIME, AUDIO_PATTERN
    from sales_backend.services.visit_import import AUDIO_EXTENSIONS
    import re
    assert set(AUDIO_MIME) == AUDIO_EXTENSIONS
    assert all(re.search(AUDIO_PATTERN, "录音" + suffix.upper(), re.I) for suffix in AUDIO_EXTENSIONS)
    assert not re.search(AUDIO_PATTERN, "材料.pdf", re.I)
