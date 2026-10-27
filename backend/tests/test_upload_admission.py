"""Exercise real ASGI multipart parsing, spool cleanup and bounded admission."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette import formparsers

from sales_backend.api import upload_admission as admission
from sales_backend.api.dependencies import get_identity
from sales_backend.domain.authorization import Authorization, Grant


@pytest.fixture
def harness(monkeypatch, tmp_path):
    state = SimpleNamespace(
        limits=admission.UploadLimits(request_bytes=2048), identity_calls=0, fresh_calls=0, parses=0,
        permissions={"visit.upload", "access.mini_program", "access.console"}, active=True, files=[],
        seen=[], metadata=[], retained=tmp_path / "uncertain-original", downstream_error=False,
    )
    slots = admission.UploadSlots()
    monkeypatch.setattr(admission, "_slots", slots)
    monkeypatch.setattr(admission.UploadLimits, "configured", classmethod(lambda cls: state.limits))

    class Database:
        @asynccontextmanager
        async def transaction(self, actor, **kwargs):
            yield SimpleNamespace(actor=actor)

    class Permissions:
        async def effective(self, connection):
            return Authorization.combine(connection.actor.workspace_id, "user",
                                         [Grant(code, "self" if code == "visit.upload" else "workspace")
                                          for code in state.permissions])

    async def identify(request, credentials, database, settings):
        state.identity_calls += 1
        if not credentials:
            raise HTTPException(401, "AUTH_REQUIRED")
        identity = SimpleNamespace(actor=SimpleNamespace(workspace_id=credentials.credentials),
                                   client_channel="web" if credentials.credentials == "web" else "wechat-mini-program",
                                   auth_method="password")
        request.state.verified_identity = identity
        return identity

    @asynccontextmanager
    async def fresh(request, identity, database):
        state.fresh_calls += 1
        if not state.active:
            raise PermissionError("session revoked")
        async with database.transaction(identity.actor) as connection:
            effective = await Permissions().effective(connection)
            effective.require("access.console" if identity.client_channel == "web" else "access.mini_program")
            yield connection, effective

    original_spool, original_parse = formparsers.SpooledTemporaryFile, formparsers.MultiPartParser.parse

    def spool(*args, **kwargs):
        # Force disk rollover for tiny synthetic payloads; inspect real closure.
        value = original_spool(max_size=8, dir=tmp_path)
        state.files.append(value)
        return value

    async def parse(parser):
        state.parses += 1
        return await original_parse(parser)

    monkeypatch.setattr(admission, "get_identity", identify)
    monkeypatch.setattr(admission, "AuthorizationRepository", Permissions)
    monkeypatch.setattr(admission, "live_identity_connection", fresh)
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    monkeypatch.setattr(formparsers.MultiPartParser, "parse", parse)
    app = FastAPI()
    app.state.database, app.state.settings = Database(), object()

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    async def receive_file(file: UploadFile = File(...), original_filename: str = Form(""),
                           source_kind: Literal["recording", "file"] | None = Form(None),
                           asr_session_id: UUID | None = Form(None),
                           identity=Depends(get_identity)):
        value = await file.read()
        state.seen.append((identity.actor.workspace_id, original_filename or file.filename, value))
        state.metadata.append((source_kind, str(asr_session_id) if asr_session_id else None))
        if state.downstream_error:
            state.retained.write_bytes(value)
            raise HTTPException(503, "uncertain registration")
        return {"filename": original_filename or file.filename, "size": len(value)}

    router = APIRouter()
    router.add_api_route("/upload", receive_file, methods=["POST"], status_code=202,
                         route_class_override=admission.VisitUploadRoute)
    app.include_router(router)
    state.app, state.slots = app, slots
    return state


def multipart(*, filename="record.mp3", content=b"synthetic-content", data=None, files=None):
    value = httpx.Request("POST", "http://test/upload", files=files or {"file": (filename, content)}, data=data)
    return value.read(), value.headers["content-type"]


async def request(state, body=None, content_type=None, *, chunks=None, token="a", length=None, hook=None):  # noqa: S107
    if body is None:
        body, content_type = multipart()
    queue = list(chunks or [body])
    result = SimpleNamespace(reads=0, messages=[])
    headers = [(b"content-type", content_type.encode())]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if length is not None:
        headers.append((b"content-length", str(length).encode()))

    async def receive():
        result.reads += 1
        if hook:
            await hook(result.reads)
        if not queue:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}

    async def send(message):
        result.messages.append(message)

    await state.app({"type": "http", "asgi": {"spec_version": "2.4"}, "http_version": "1.1",
                     "method": "POST", "scheme": "http", "path": "/upload", "raw_path": b"/upload",
                     "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
                     "server": ("test", 80)}, receive, send)
    result.status = next(value["status"] for value in result.messages if value["type"] == "http.response.start")
    result.body = b"".join(value.get("body", b"") for value in result.messages)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["no-login", "no-upload", "no-mini", "no-console"])
async def test_rejected_identity_or_capability_never_reads_or_spools_body(harness, denial):
    token = "a"
    if denial == "no-login":
        token = None
    else:
        harness.permissions.remove({"no-upload": "visit.upload", "no-mini": "access.mini_program",
                                    "no-console": "access.console"}[denial])
        if denial == "no-console":
            token = "web"
    result = await request(harness, token=token)
    assert result.status == (401 if denial == "no-login" else 403)
    assert result.reads == 0 and not harness.files and not harness.seen


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["record.mp3", "材料.pdf"])
@pytest.mark.parametrize("override", [None, "原始拜访.mp3"])
async def test_valid_frontend_file_and_filename_contract_parses_only_once(harness, filename, override):
    body, kind = multipart(filename=filename, data={"original_filename": override} if override else None)
    result = await request(harness, body, kind, length=len(body))
    assert result.status == 202 and result.reads == 1
    assert harness.seen == [("a", override or filename, b"synthetic-content")]
    assert harness.parses == 1 and harness.identity_calls == 1 and harness.fresh_calls == 1
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["recording", "file"])
async def test_asr_upload_metadata_and_webm_reach_downstream_without_extra_parse(harness, source_kind):
    session_id = "7831cf70-81d3-4daf-a9b0-b376d50630f7"
    body, kind = multipart(filename="record.webm", data={
        "original_filename": "跟进录音.webm", "source_kind": source_kind, "asr_session_id": session_id,
    })
    result = await request(harness, body, kind, token="web")
    assert result.status == 202
    assert harness.seen == [("web", "跟进录音.webm", b"synthetic-content")]
    assert harness.metadata == [(source_kind, session_id)]
    assert harness.parses == harness.identity_calls == harness.fresh_calls == 1
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("source_kind", "unexpected"), ("asr_session_id", "invalid-uuid")])
async def test_asr_metadata_values_are_validated_by_route_contract(harness, field, value):
    body, kind = multipart(data={field: value})
    result = await request(harness, body, kind)
    assert result.status == 422 and not harness.seen
    assert harness.fresh_calls == 1  # Reception succeeded; endpoint type validation rejected the value.
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["original_filename", "source_kind", "asr_session_id"])
@pytest.mark.parametrize("case", ["duplicate", "file-value", "oversized"])
async def test_each_upload_text_field_keeps_single_value_type_and_size_bounds(harness, field, case):
    files = [("file", ("record.webm", b"synthetic"))]
    if case == "duplicate":
        files.extend([(field, (None, "one")), (field, (None, "two"))])
    elif case == "file-value":
        files.append((field, ("unexpected.txt", b"value")))
    else:
        harness.limits = replace(harness.limits, field_bytes=8)
        files.append((field, (None, "x" * 9)))
    body, kind = multipart(files=files)
    result = await request(harness, body, kind)
    assert result.status in {400, 422} and not harness.seen
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
async def test_reception_rejects_more_than_three_text_fields(harness):
    body, kind = multipart(data={
        "original_filename": "record.webm", "source_kind": "recording",
        "asr_session_id": "7831cf70-81d3-4daf-a9b0-b376d50630f7", "unexpected": "value",
    })
    result = await request(harness, body, kind)
    assert result.status == 400 and not harness.seen
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, 1])
async def test_actual_receive_bytes_bound_chunked_and_false_content_length(harness, length):
    body, kind = multipart(content=b"x" * 4096)
    chunks = [body[i:i + 512] for i in range(0, len(body), 512)]
    result = await request(harness, body, kind, chunks=chunks, length=length)
    assert result.status == 413 and result.reads < len(chunks)
    assert harness.files and all(value.closed for value in harness.files)
    assert not harness.seen and harness.slots._total == 0
    assert (await request(harness)).status == 202


@pytest.mark.asyncio
async def test_large_declared_body_is_rejected_before_reading(harness):
    result = await request(harness, length=2049)
    assert result.status == 413 and result.reads == 0 and not harness.files


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["extra-file", "duplicate-file", "extra-field", "duplicate-name", "large-field"])
async def test_multipart_shape_is_bounded_and_files_close_on_rejection(harness, case):
    files = [("file", ("one.mp3", b"test"))]
    data = None
    if case in {"extra-file", "duplicate-file"}:
        files.append(("other" if case == "extra-file" else "file", ("two.mp3", b"test")))
    elif case == "extra-field":
        data = {"unexpected": "value"}
    elif case == "duplicate-name":
        files += [("original_filename", (None, "one.mp3")), ("original_filename", (None, "two.mp3"))]
    else:
        harness.limits = replace(harness.limits, field_bytes=8)
        data = {"original_filename": "x" * 9}
    body, kind = multipart(files=files, data=data)
    result = await request(harness, body, kind)
    assert result.status in {400, 422} and not harness.seen
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["logout", "permission"])
async def test_identity_is_rechecked_after_body_without_second_admission(harness, change):
    async def changed(reads):
        if change == "logout":
            harness.active = False
        else:
            harness.permissions.remove("visit.upload")

    result = await request(harness, hook=changed)
    assert result.status == 403 and harness.identity_calls == 1 and harness.fresh_calls == 1
    assert not harness.seen and all(value.closed for value in harness.files)


def split_body():
    body, kind = multipart(content=b"x" * 100)
    end = body.index(b"\r\n\r\n") + 20
    return body, kind, [body[:end], body[end:]]


@pytest.mark.asyncio
async def test_idle_timeout_closes_partial_spool_and_releases_slot(harness):
    harness.limits = replace(harness.limits, idle_seconds=0.01)

    async def stall(reads):
        if reads == 2:
            await asyncio.Future()

    body, kind, chunks = split_body()
    result = await request(harness, body, kind, chunks=chunks, hook=stall)
    assert result.status == 408 and harness.files and all(value.closed for value in harness.files)
    assert (await request(harness)).status == 202


@pytest.mark.asyncio
async def test_total_timeout_also_bounds_continually_trickling_body(harness):
    harness.limits = replace(harness.limits, receive_seconds=0.03, idle_seconds=0.02)
    body, kind = multipart(content=b"x" * 100)

    async def slow(reads):
        await asyncio.sleep(0.008)

    chunks = [body[i:i + 20] for i in range(0, len(body), 20)]
    result = await request(harness, body, kind, chunks=chunks, hook=slow)
    assert result.status == 408 and result.reads < len(chunks)
    assert harness.slots._total == 0 and not harness.seen


@pytest.mark.asyncio
async def test_tenant_limit_preserves_another_tenant_slot_and_global_limit_has_no_wait_queue(harness):
    harness.limits = replace(harness.limits, max_active=2, tenant_active=1)
    started_a, started_b, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def stall(started):
        async def receive(reads):
            if reads == 2:
                started.set()
                await release.wait()
        return receive

    body, kind, chunks = split_body()
    first = asyncio.create_task(request(harness, body, kind, chunks=chunks, hook=stall(started_a)))
    await asyncio.wait_for(started_a.wait(), 1)
    denied_a = await request(harness)
    assert denied_a.status == 429 and denied_a.reads == 0
    second = asyncio.create_task(request(harness, body, kind, chunks=chunks, token="b", hook=stall(started_b)))
    await asyncio.wait_for(started_b.wait(), 1)  # A did not occupy B's available slot.
    denied_c = await request(harness, token="c")
    assert denied_c.status == 429 and denied_c.reads == 0
    release.set()
    assert (await first).status == (await second).status == 202
    assert harness.slots._total == 0 and not harness.slots._tenants


@pytest.mark.asyncio
async def test_request_cancellation_and_unknown_commit_preserve_owned_resources(harness):
    started = asyncio.Event()

    async def stall(reads):
        if reads == 2:
            started.set()
            await asyncio.Future()

    body, kind, chunks = split_body()
    pending = asyncio.create_task(request(harness, body, kind, chunks=chunks, hook=stall))
    await asyncio.wait_for(started.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert harness.files and all(value.closed for value in harness.files) and harness.slots._total == 0
    harness.downstream_error = True
    failed = await request(harness)
    assert failed.status == 503 and harness.retained.read_bytes() == b"synthetic-content"
    assert all(value.closed for value in harness.files) and harness.slots._total == 0


@pytest.mark.asyncio
async def test_cancellation_after_complete_parse_closes_spool_and_releases_slot(harness, monkeypatch):
    parsed = asyncio.Event()

    async def blocked_final_authorization(*args):
        parsed.set()
        await asyncio.Future()

    with monkeypatch.context() as patch:
        patch.setattr(admission, "_revalidate_upload", blocked_final_authorization)
        pending = asyncio.create_task(request(harness))
        await asyncio.wait_for(parsed.wait(), 1)
        assert harness.parses == 1 and harness.files and not harness.files[0].closed
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert all(value.closed for value in harness.files)
    assert harness.slots._total == 0 and not harness.slots._tenants and not harness.seen
    assert (await request(harness)).status == 202


@pytest.mark.parametrize("name,value", [("SALES_UPLOAD_MAX_ACTIVE", "0"), ("SALES_UPLOAD_MAX_ACTIVE", "33"),
                                      ("SALES_UPLOAD_TENANT_ACTIVE", "5"),
                                      ("SALES_UPLOAD_RECEIVE_SECONDS", "nan"),
                                      ("SALES_UPLOAD_IDLE_SECONDS", "inf"),
                                      ("SALES_UPLOAD_IDLE_SECONDS", "invalid")])
def test_invalid_configuration_fails_closed_without_exposing_environment_values(monkeypatch, name, value):
    for key in ("SALES_UPLOAD_MAX_ACTIVE", "SALES_UPLOAD_TENANT_ACTIVE",
                "SALES_UPLOAD_RECEIVE_SECONDS", "SALES_UPLOAD_IDLE_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    defaults = admission.UploadLimits.configured()
    assert defaults.request_bytes == 100 * 1024 * 1024 + 64 * 1024
    monkeypatch.setenv(name, value)
    with pytest.raises(HTTPException) as error:
        admission.UploadLimits.configured()
    assert error.value.status_code == 503 and error.value.detail == "上传接收配置无效，请联系管理员"
