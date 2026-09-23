"""Real local files; cancellation and DB outcome boundaries are independent."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import visit_imports
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.services import visit_upload
from sales_backend.services.visit_upload import LocalVisitImportStorage, UploadRejected, VisitUploadService

pytestmark = pytest.mark.asyncio


class Source:
    def __init__(self, content=b"customer note", *, block=False):
        self.content = content
        self.block = block
        self.waiting = asyncio.Event()

    async def read(self, size):
        if self.content:
            content, self.content = self.content, b""
            return content
        if self.block:
            self.waiting.set()
            await asyncio.Future()
        return b""


class MemoryDatabase:
    def __init__(self):
        self.rows = []
        self.after_commit = None
        self.before_commit = None
        self.calls = 0

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        self.calls += 1
        pending = SimpleNamespace(rows=[])
        yield pending
        if not readonly:
            if self.before_commit:
                await self.before_commit()
            self.rows.extend(pending.rows)
            if self.after_commit:
                raise self.after_commit


@pytest.fixture
def harness(monkeypatch, tmp_path):
    async def allowed(*args):
        pass

    async def register(connection, actor, import_id, filename, path, size):
        assert path.read_bytes() and path.stat().st_size == size
        assert not list(tmp_path.glob("*.part"))
        connection.rows.append(dict(id=import_id, filename=filename, path=path, size=size))

    monkeypatch.setattr(visit_upload, "require_capability", allowed)
    monkeypatch.setattr(visit_upload, "create_import", register)
    database = MemoryDatabase()
    service = VisitUploadService(database, LocalVisitImportStorage(tmp_path))
    return service, database


async def test_success_publishes_complete_private_original_and_keeps_it(harness, tmp_path):
    service, database = harness
    result = await service.upload(None, source=Source(), filename="../customer.txt")
    assert result == {"id": database.rows[0]["id"], "status": "queued", "filename": "customer.txt"}
    path = database.rows[0]["path"]
    assert path.read_bytes() == b"customer note"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


async def test_cancel_mid_upload_cleans_only_its_partial(harness, tmp_path):
    service, database = harness
    other = tmp_path / "existing.txt"
    other.write_text("registered original")
    source = Source(block=True)
    task = asyncio.create_task(service.upload(None, source=source, filename="customer.txt"))
    await asyncio.wait_for(source.waiting.wait(), 2)
    assert len(list(tmp_path.glob("*.part"))) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert database.rows == []
    assert list(tmp_path.iterdir()) == [other]
    assert other.read_text() == "registered original"


@pytest.mark.parametrize(
    "filename,content,status", [("x.txt", b"", 422), ("x.exe", b"hello", 422), ("x.txt", b"12345", 413)]
)
async def test_rejection_does_not_leave_files(harness, tmp_path, monkeypatch, filename, content, status):
    service, database = harness
    monkeypatch.setattr(visit_upload, "MAX_DOCUMENT", 4)
    with pytest.raises(UploadRejected) as error:
        await service.upload(None, source=Source(content), filename=filename)
    assert error.value.status_code == status
    assert not list(tmp_path.iterdir()) and not database.rows


@pytest.mark.parametrize("stage", ["permission", "insert", "write", "ready"])
async def test_pre_commit_failures_clean_ready_or_partial(harness, tmp_path, monkeypatch, stage):
    service, database = harness
    if stage == "permission":
        calls = 0

        async def permission(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("policy changed")

        monkeypatch.setattr(visit_upload, "require_capability", permission)
    elif stage == "insert":

        async def failed(*args):
            raise ValueError("insertion failed")

        monkeypatch.setattr(visit_upload, "create_import", failed)
    else:

        async def failed(*args):
            raise ValueError("file operation failed")

        monkeypatch.setattr(visit_upload.LocalVisitUpload, stage, failed)
    with pytest.raises((ValueError, PermissionError)):
        await service.upload(None, source=Source(), filename="x.txt")
    assert not list(tmp_path.iterdir()) and not database.rows


async def test_cancel_during_insert_rolls_back_and_cleans(harness, tmp_path, monkeypatch):
    service, database = harness
    writing = asyncio.Event()

    async def blocked(*args):
        writing.set()
        await asyncio.Future()

    monkeypatch.setattr(visit_upload, "create_import", blocked)
    task = asyncio.create_task(service.upload(None, source=Source(), filename="x.txt"))
    await asyncio.wait_for(writing.wait(), 2)
    assert len(list(tmp_path.glob("*.txt"))) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not database.rows and not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [RuntimeError("response lost"), asyncio.CancelledError()])
async def test_commit_completed_then_error_retains_original(harness, tmp_path, caplog, failure):
    service, database = harness
    database.after_commit = failure
    with pytest.raises(type(failure)):
        await service.upload(None, source=Source(), filename="x.txt")
    assert len(database.rows) == 1
    assert database.rows[0]["path"].read_bytes() == b"customer note"
    assert "visit_upload_commit_unknown" in caplog.text
    assert database.rows[0]["id"] in caplog.text
    assert "customer note" not in caplog.text


async def test_uncertain_commit_does_not_guess_that_missing_result_means_rollback(harness, tmp_path):
    service, database = harness
    pending = asyncio.Event()

    async def before_commit():
        pending.set()
        await asyncio.Future()

    database.before_commit = before_commit
    task = asyncio.create_task(service.upload(None, source=Source(), filename="x.txt"))
    await asyncio.wait_for(pending.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not database.rows
    assert len(list(tmp_path.glob("*.txt"))) == 1  # retained for explicit reconciliation
    assert not list(tmp_path.glob("*.part"))


async def test_filename_collision_does_not_overwrite_or_delete_existing_original(harness, tmp_path):
    service, database = harness
    identity = uuid4()
    service._id_factory = lambda: identity
    existing = tmp_path / (str(identity) + ".txt")
    existing.write_bytes(b"another upload")
    with pytest.raises(FileExistsError):
        await service.upload(None, source=Source(), filename="x.txt")
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"another upload" and not database.rows


async def test_cleanup_error_keeps_primary_error(harness, tmp_path, monkeypatch, caplog):
    service, _ = harness

    def failed_cleanup(self):
        self.stream.close()
        raise OSError("storage unavailable")

    monkeypatch.setattr(visit_upload.LocalVisitUpload, "_discard_unregistered", failed_cleanup)
    with pytest.raises(UploadRejected, match="文件为空"):
        await service.upload(None, source=Source(b""), filename="x.txt")
    assert "visit_upload_cleanup_failed" in caplog.text
    # Only this test's abandoned partial is removed by pytest's tmp_path cleanup.


async def test_http_upload_keeps_202_and_validation_contract(harness, tmp_path, monkeypatch):
    _, database = harness
    monkeypatch.setattr(visit_imports, "IMPORT_ROOT", tmp_path)
    monkeypatch.setattr(visit_imports, "MaterialStorage", lambda: None)
    app = FastAPI()
    app.include_router(visit_imports.router)
    app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=None)
    app.dependency_overrides[get_database] = lambda: database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/visit-imports", files={"file": ("x.txt", b"notes")}, data={"original_filename": "a.txt"}
        )
        assert response.status_code == 202 and response.json()["status"] == "queued"
        assert response.json()["filename"] == "a.txt"
        invalid = await client.post("/api/v1/visit-imports", files={"file": ("x.zip", b"notes")})
        assert invalid.status_code == 422
    assert len(database.rows) == 1


async def test_cancel_during_publish_waits_for_file_io_then_cleans_its_file(harness, tmp_path, monkeypatch):
    service, database = harness
    started, release = threading.Event(), threading.Event()
    original_finish = visit_upload.LocalVisitUpload._finish_file

    def delayed_finish(upload):
        started.set()
        assert release.wait(3)
        original_finish(upload)

    monkeypatch.setattr(visit_upload.LocalVisitUpload, "_finish_file", delayed_finish)
    task = asyncio.create_task(service.upload(None, source=Source(), filename="x.txt"))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(tmp_path.glob("*.part"))
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert not list(tmp_path.iterdir()) and not database.rows
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
