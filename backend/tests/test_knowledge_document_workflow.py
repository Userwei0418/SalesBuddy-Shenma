"""Observed public Workflow envelope with synthetic transport and Office bytes.

These tests are not real provider/Office UI acceptance. The live 2026-10-17 run
returned a DOCX descriptor blocked by Basic 401; the 2026-10-18 contract now
refreshes signed files with GET of the same run. Live acceptance is separate.
"""

import asyncio
import dataclasses
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from sales_backend.config import Settings
from sales_backend.services.knowledge_artifacts import MIME_TYPES
from sales_backend.services.knowledge_document_workflow import DocumentWorkflowClient, DocumentWorkflowError

from .test_knowledge_artifacts import docx, package

RUN = "b1d6b0f8-2c17-4cbd-b8b7-32cadcb15cea"
BODY = package(docx())
FILE_URL = "https://files.example/files/tools/fixture.docx?signature=private-test-signature"


def settings(**overrides):
    fields = dict(
        knowledge_document_workflow_base_url="https://workflow.example/v1",
        knowledge_document_workflow_api_key="workflow-test-key",
        knowledge_document_workflow_source_only_reviewed=True,
        knowledge_document_workflow_download_origins='["https://files.example"]',
        knowledge_document_workflow_timeout_seconds=2,
    )
    return SimpleNamespace(**(fields | overrides))


def file(**overrides):
    # Exact field names observed in outputs.files; no private response envelope.
    return (
        dict(
            dify_model_identity="__dify__file__",
            extension=".docx",
            filename="fixture.docx",
            id="synthetic",
            mime_type=MIME_TYPES["docx"],
            reference=None,
            related_id="synthetic",
            remote_url=None,
            size=len(BODY),
            transfer_method="tool_file",
            type="document",
            url=FILE_URL,
        )
        | overrides
    )


def events(*, status="succeeded", outputs=None):
    return [
        {"event": "workflow_started", "workflow_run_id": RUN, "task_id": "synthetic"},
        {"event": "node_finished", "workflow_run_id": RUN, "data": {"status": "succeeded"}},
        {
            "event": "workflow_finished",
            "workflow_run_id": RUN,
            "data": {
                "status": status,
                "elapsed_time": 14.18117,
                "total_steps": 3,
                "total_tokens": 21826,
                "outputs": {"report": "虚构验收摘要", "files": [file()]} if outputs is None else outputs,
            },
        },
    ]


def wire(rows):
    return b"event: ping\n\n" + b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows)


class Fixture:
    def __init__(self, *, rows=None, workflow_status=200, file_status=200, binary=BODY, details=None, **config):
        self.requests = []
        self.reads = []
        self.downloads = []
        self.after_download = None
        self.rows = events() if rows is None else rows
        self.binary = binary

        def workflow(request):
            if request.method == "GET":
                self.reads.append(request)
                result = (
                    details[min(len(self.reads) - 1, len(details) - 1)]
                    if details
                    else {"id": RUN, **self.rows[-1]["data"]}
                )
                return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
            self.requests.append(request)
            return httpx.Response(
                workflow_status, content=wire(self.rows), headers={"content-type": "text/event-stream"}
            )

        async def download(request):
            self.downloads.append(request)
            if self.after_download:
                await self.after_download()
            return httpx.Response(
                file_status[min(len(self.downloads) - 1, len(file_status) - 1)]
                if isinstance(file_status, list)
                else file_status,
                content=self.binary,
                headers={"content-type": MIME_TYPES["docx"], "www-authenticate": "Basic"},
            )

        self.client = DocumentWorkflowClient(
            settings(**config),
            transport=httpx.MockTransport(workflow),
            download_transport=httpx.MockTransport(download),
        )


@pytest.mark.asyncio
async def test_observed_envelope_archives_original_validated_bytes_no_auth_forwarding():
    f = Fixture()
    result = await f.client.generate("仅使用这些合成事实", user="tenant-user")
    assert result.run_id == RUN and result.files[0].content == BODY
    assert result.files[0].editable_text_count > 0
    assert len(f.requests) == len(f.reads) == len(f.downloads) == 1
    assert f.reads[0].method == "GET"
    assert str(f.reads[0].url) == "https://workflow.example/v1/workflows/run/" + RUN
    assert f.reads[0].headers["authorization"] == "Bearer workflow-test-key"
    request = f.requests[0]
    assert str(request.url) == "https://workflow.example/v1/workflows/run"
    assert json.loads(request.content) == {
        "inputs": {"source_text": "仅使用这些合成事实"},
        "response_mode": "streaming",
        "user": "tenant-user",
    }
    assert request.headers["authorization"] == "Bearer workflow-test-key"
    assert not any(k in f.downloads[0].headers for k in ["authorization", "cookie", "x-api-key"])


@pytest.mark.parametrize(
    "config",
    [
        {"knowledge_document_workflow_source_only_reviewed": False},
        {"knowledge_document_workflow_api_key": ""},
        {"knowledge_document_workflow_api_key": "bad\nkey"},
        {"knowledge_document_workflow_base_url": "http://workflow.example/v1"},
        {"knowledge_document_workflow_base_url": "https://user:secret@workflow.example/v1"},
        {"knowledge_document_workflow_base_url": "https://workflow.example/console/api"},
        {"knowledge_document_workflow_base_url": "https://workflow.example/v1?key=secret"},
        {"knowledge_document_workflow_base_url": "https://workflow.example/v1#"},
        {"knowledge_document_workflow_download_origins": ""},
        {"knowledge_document_workflow_download_origins": '"https://files.example"'},
        {"knowledge_document_workflow_download_origins": '["http://files.example"]'},
        {"knowledge_document_workflow_download_origins": '["https://files.example/files"]'},
        {"knowledge_document_workflow_timeout_seconds": 61},
        {"knowledge_document_workflow_timeout_seconds": float("nan")},
    ],
)
@pytest.mark.asyncio
async def test_incomplete_or_unsafe_configuration_makes_no_request(config):
    f = Fixture(**config)
    assert not f.client.configured
    with pytest.raises(DocumentWorkflowError, match="GENERATION_UNCONFIGURED"):
        await f.client.generate("data", user="test")
    assert f.requests == f.downloads == []


def test_workflow_configuration_is_default_disabled_and_key_not_repr():
    fields = {f.name: f for f in dataclasses.fields(Settings)}
    assert fields["knowledge_document_workflow_source_only_reviewed"].default is False
    assert fields["knowledge_document_workflow_api_key"].repr is False
    assert fields["knowledge_document_workflow_base_url"].default == ""


@pytest.mark.parametrize("text", ["", "x" * 10001])
@pytest.mark.asyncio
async def test_evidence_limit_never_silently_truncates_or_calls_provider(text):
    f = Fixture()
    with pytest.raises(DocumentWorkflowError, match="SOURCE_LIMIT"):
        await f.client.generate(text, user="test")
    assert not f.requests


@pytest.mark.parametrize(
    "status,code",
    [(401, "AUTH_UNAVAILABLE"), (403, "AUTH_UNAVAILABLE"), (302, "HTTP_UNAVAILABLE"), (500, "HTTP_UNAVAILABLE")],
)
@pytest.mark.asyncio
async def test_workflow_http_failure_has_no_retry_or_download(status, code):
    f = Fixture(workflow_status=status)
    with pytest.raises(DocumentWorkflowError, match=code):
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == 1 and not f.downloads


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", "RUN_FAILED"),
        ("partial-succeeded", "RUN_FAILED"),
        ("paused", "RUN_PAUSED"),
        ("stopped", "RUN_STOPPED"),
        ("running", "RESULT_UNKNOWN"),
    ],
)
@pytest.mark.asyncio
async def test_terminal_states_fail_explicitly(status, code):
    f = Fixture(rows=events(status=status))
    with pytest.raises(DocumentWorkflowError, match=code) as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.run_id == RUN and len(f.requests) == 1 and not f.downloads


@pytest.mark.parametrize(
    "outputs",
    [
        {"report": "text only", "files": []},
        {"report": "text only"},
        {"report": {}, "files": [file()]},
        {"report": "x", "files": [file()] * 5},
        {"report": "x", "files": [file(extension=".pptx")]},
        {"report": "x", "files": [file(size=True)]},
    ],
)
@pytest.mark.asyncio
async def test_invalid_output_never_wraps_text_as_docx(outputs):
    f = Fixture(rows=events(outputs=outputs))
    with pytest.raises(DocumentWorkflowError):
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == 1 and not f.downloads


@pytest.mark.asyncio
async def test_observed_basic_401_retains_run_id_only_no_retry_or_credentials_leak():
    f = Fixture(file_status=401)
    with pytest.raises(DocumentWorkflowError, match="DOWNLOAD_UNAVAILABLE") as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.run_id == RUN and len(f.requests) == len(f.downloads) == 1
    assert "private-test-signature" not in str(exc.value) and "workflow-test-key" not in str(exc.value)
    assert "authorization" not in f.downloads[0].headers


@pytest.mark.asyncio
async def test_unapproved_file_host_fails_before_download():
    f = Fixture(rows=events(outputs={"report": "x", "files": [file(url="https://unapproved.example/x.docx")]}))
    with pytest.raises(DocumentWorkflowError, match="URL_NOT_ALLOWED"):
        await f.client.generate("fixture", user="test")
    assert not f.downloads


@pytest.mark.asyncio
async def test_fake_docx_bytes_are_rejected():
    f = Fixture(binary=b"not docx")
    with pytest.raises(DocumentWorkflowError, match="KNOWLEDGE_ARTIFACT_"):
        await f.client.generate("fixture", user="test")


@pytest.mark.asyncio
async def test_run_id_change_and_eof_are_not_success():
    for rows in [
        events()[:1],
        [events()[2]],
        [events()[0], events()[2] | {"workflow_run_id": str(__import__("uuid").uuid4())}],
    ]:
        f = Fixture(rows=rows)
        with pytest.raises(DocumentWorkflowError):
            await f.client.generate("fixture", user="test")
        assert not f.downloads


@pytest.mark.asyncio
async def test_error_event_does_not_leak_raw_provider_body():
    f = Fixture(rows=[events()[0], {"event": "error", "message": "private-secret-url-token", "code": "sensitive"}])
    with pytest.raises(DocumentWorkflowError, match="RUN_FAILED") as exc:
        await f.client.generate("fixture", user="test")
    assert "private" not in str(exc.value)


@pytest.mark.asyncio
async def test_total_deadline_includes_file_download():
    f = Fixture(knowledge_document_workflow_timeout_seconds=0.01)
    f.after_download = lambda: asyncio.sleep(0.1)
    with pytest.raises(DocumentWorkflowError, match="TIMEOUT") as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.run_id == RUN and len(f.requests) == len(f.downloads) == 1


@pytest.mark.asyncio
async def test_response_and_event_memory_bounds():
    for content in [b"data: " + b"x" * (1024 * 1024 + 1), b":" + b"x\n" * (2 * 1024 * 1024)]:

        async def handler(request, content=content):
            return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

        client = DocumentWorkflowClient(settings(), transport=httpx.MockTransport(handler))
        with pytest.raises(DocumentWorkflowError, match="RESPONSE_LIMIT"):
            await client.generate("fixture", user="test")


def details(*, run_id=RUN, status="succeeded", files=None, text_outputs=False):
    outputs = {"report": "虚构验收摘要", "files": [file()] if files is None else files}
    return {"id": run_id, "status": status, "outputs": json.dumps(outputs) if text_outputs else outputs}


@pytest.mark.asyncio
async def test_generate_uses_refreshed_get_url_not_sse_url_and_decodes_string_outputs():
    refreshed_url = FILE_URL.replace("private-test-signature", "refreshed-private-signature")
    f = Fixture(details=[details(files=[file(url=refreshed_url)], text_outputs=True)])
    result = await f.client.generate("fixture", user="test")
    assert result.files[0].content == BODY
    assert len(f.requests) == len(f.reads) == len(f.downloads) == 1
    assert str(f.downloads[0].url) == refreshed_url


@pytest.mark.asyncio
async def test_internal_known_run_download_never_generates_or_enables_source_only():
    f = Fixture(knowledge_document_workflow_source_only_reviewed=False, details=[details(text_outputs=True)])
    assert not f.client.configured
    result = await f.client.download_run(RUN)
    assert result.run_id == RUN and result.files[0].content == BODY
    assert not f.requests and len(f.reads) == len(f.downloads) == 1
    with pytest.raises(DocumentWorkflowError, match="GENERATION_UNCONFIGURED"):
        await f.client.generate("data", user="test")
    assert not f.requests


@pytest.mark.parametrize("run_id", ["", "../other", "x?key=secret", None, 1])
@pytest.mark.asyncio
async def test_invalid_known_run_is_rejected_before_any_request(run_id):
    f = Fixture()
    with pytest.raises(DocumentWorkflowError, match="INPUT_INVALID"):
        await f.client.download_run(run_id)
    assert not f.requests and not f.reads and not f.downloads


@pytest.mark.parametrize("run_id", [None, "c1d6b0f8-2c17-4cbd-b8b7-32cadcb15cea", "bad"])
@pytest.mark.asyncio
async def test_get_wrong_run_rejected_before_file_request(run_id):
    f = Fixture(details=[details(run_id=run_id)])
    with pytest.raises(DocumentWorkflowError, match="RESPONSE_INVALID") as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.run_id == RUN
    assert len(f.requests) == len(f.reads) == 1 and not f.downloads


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "different"},
        {"related_id": "different"},
        {"filename": "different.docx"},
        {"url": "https://files.example/files/tools/different.docx?sign=secret"},
        {"url": "https://evil.example/files/tools/fixture.docx?sign=secret"},
        {"size": len(BODY) + 1},
        {"mime_type": "application/octet-stream"},
    ],
)
@pytest.mark.asyncio
async def test_get_may_only_rotate_query_not_file_identity_or_metadata(overrides):
    f = Fixture(details=[details(files=[file(**overrides)])])
    with pytest.raises(DocumentWorkflowError, match="FILES_INVALID"):
        await f.client.generate("fixture", user="test")
    assert len(f.reads) == 1 and not f.downloads


def expired_file():
    return file(url=FILE_URL + "&timestamp=1799999699&nonce=fixture")


def fresh_file():
    return file(url=FILE_URL + "&timestamp=1799999999&nonce=refreshed")


@pytest.mark.asyncio
async def test_expired_403_refreshes_same_run_once_without_post_or_auth_to_files(monkeypatch, caplog):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    f = Fixture(file_status=[403, 200], details=[details(files=[expired_file()]), details(files=[fresh_file()])])
    with caplog.at_level(logging.DEBUG):
        result = await f.client.generate("fixture", user="test")
    assert result.files[0].content == BODY
    assert len(f.requests) == 1 and len(f.reads) == len(f.downloads) == 2
    assert {str(r.url) for r in f.reads} == {"https://workflow.example/v1/workflows/run/" + RUN}
    assert all(r.method == "GET" and r.headers["authorization"] == "Bearer workflow-test-key" for r in f.reads)
    assert all(not any(k in r.headers for k in ["authorization", "cookie", "x-api-key"]) for r in f.downloads)
    assert "private-test-signature" not in caplog.text and "workflow-test-key" not in caplog.text
    assert str(f.downloads[0].url) == expired_file()["url"]
    assert str(f.downloads[1].url) == fresh_file()["url"]


@pytest.mark.asyncio
async def test_repeated_expired_403_does_not_refresh_twice_or_regenerate(monkeypatch):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    f = Fixture(file_status=403, details=[details(files=[expired_file()])])
    with pytest.raises(DocumentWorkflowError, match="DOWNLOAD_UNAVAILABLE") as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.run_id == RUN
    assert len(f.requests) == 1 and len(f.reads) == len(f.downloads) == 2


@pytest.mark.parametrize(
    "query",
    [
        "",
        "&timestamp=1799999999",
        "&timestamp=1800000001",
        "&timestamp=1799999699000",
        "&timestamp=1799999699&timestamp=1799999699",
        "&timestamp=-1",
        "&timestamp=not-a-time",
    ],
)
@pytest.mark.asyncio
async def test_403_without_verified_expiry_is_not_retried(query, monkeypatch):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    f = Fixture(file_status=403, details=[details(files=[file(url=FILE_URL + query)])])
    with pytest.raises(DocumentWorkflowError, match="DOWNLOAD_UNAVAILABLE"):
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == len(f.reads) == len(f.downloads) == 1


@pytest.mark.parametrize("status", [401, 404, 429, 500, 302])
@pytest.mark.asyncio
async def test_non403_file_failure_never_refreshes_even_expired(status, monkeypatch):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    f = Fixture(file_status=status, details=[details(files=[expired_file()])])
    with pytest.raises(DocumentWorkflowError) as exc:
        await f.client.generate("fixture", user="test")
    assert exc.value.code == (
        "KNOWLEDGE_ARTIFACT_DOWNLOAD_REDIRECT" if status == 302 else "KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE"
    )
    assert len(f.requests) == len(f.reads) == len(f.downloads) == 1


@pytest.mark.parametrize(
    "status,code",
    [(401, "AUTH_UNAVAILABLE"), (403, "AUTH_UNAVAILABLE"), (404, "HTTP_UNAVAILABLE"), (302, "HTTP_UNAVAILABLE")],
)
@pytest.mark.asyncio
async def test_details_failure_never_refreshes_or_downloads(status, code, caplog):
    f = Fixture(details=[httpx.Response(status, text="private-test-signature workflow-test-key")])
    with caplog.at_level(logging.DEBUG), pytest.raises(DocumentWorkflowError, match=code) as exc:
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == len(f.reads) == 1 and not f.downloads
    assert "private-test-signature" not in caplog.text + str(exc.value)
    assert "workflow-test-key" not in caplog.text + str(exc.value)


@pytest.mark.parametrize(
    "changed",
    [
        details(run_id="c1d6b0f8-2c17-4cbd-b8b7-32cadcb15cea"),
        details(files=[file(id="changed")]),
        details(status="failed"),
    ],
)
@pytest.mark.asyncio
async def test_expiry_refresh_cannot_replace_run_file_or_success_state(changed, monkeypatch):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    f = Fixture(file_status=403, details=[details(files=[expired_file()]), changed])
    with pytest.raises(DocumentWorkflowError):
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == len(f.downloads) == 1 and len(f.reads) == 2


@pytest.mark.parametrize(
    "response,code",
    [
        (
            httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1), headers={"content-type": "application/json"}),
            "RESPONSE_LIMIT",
        ),
        (httpx.Response(200, json={"id": RUN, "status": "succeeded", "outputs": "bad json"}), "RESPONSE_INVALID"),
        (httpx.Response(200, json=details(), headers={"content-type": "text/html"}), "RESPONSE_INVALID"),
    ],
)
@pytest.mark.asyncio
async def test_run_detail_limits_and_format_are_enforced(response, code):
    f = Fixture(details=[response])
    with pytest.raises(DocumentWorkflowError, match=code):
        await f.client.download_run(RUN)
    assert not f.requests and len(f.reads) == 1 and not f.downloads


@pytest.mark.asyncio
async def test_one_refresh_budget_is_shared_by_all_files(monkeypatch):
    monkeypatch.setattr("sales_backend.services.knowledge_document_workflow.time.time", lambda: 1800000000)
    second = expired_file() | {
        "id": "second",
        "filename": "second.docx",
        "url": expired_file()["url"].replace("fixture.docx", "second.docx"),
    }
    both = [expired_file(), second]
    f = Fixture(
        rows=events(outputs={"report": "fixture", "files": both}),
        details=[details(files=both)],
        file_status=[403, 200, 403],
    )
    with pytest.raises(DocumentWorkflowError, match="DOWNLOAD_UNAVAILABLE"):
        await f.client.generate("fixture", user="test")
    assert len(f.requests) == 1 and len(f.reads) == 2 and len(f.downloads) == 3


@pytest.mark.asyncio
async def test_known_run_deadline_includes_details_read():
    requests = []

    async def slow_details(request):
        requests.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=details())

    client = DocumentWorkflowClient(
        settings(knowledge_document_workflow_timeout_seconds=0.01), transport=httpx.MockTransport(slow_details)
    )
    with pytest.raises(DocumentWorkflowError, match="TIMEOUT") as exc:
        await client.download_run(RUN)
    assert exc.value.run_id == RUN
    assert len(requests) == 1 and requests[0].method == "GET"
