"""Network fixtures only; no external provider download or Office acceptance."""

import asyncio
import logging
from dataclasses import replace

import httpx
import pytest

from sales_backend.services.knowledge_artifact_download import (
    KnowledgeArtifactDownloadError,
    download_knowledge_artifact,
)
from sales_backend.services.knowledge_artifacts import MIME_TYPES, ArtifactLimits, KnowledgeArtifactError

from .test_knowledge_artifacts import docx, package, pptx

ORIGIN = "https://files.example.test:8443"
URL = ORIGIN + "/generated/report.docx?signature=private-signature"
DOCX = package(docx())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0, failure=None):
        self.chunks = chunks
        self.delay = delay
        self.failure = failure
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


class Network:
    def __init__(self, response=None, *, failure=None):
        self.response = (
            response
            if response is not None
            else httpx.Response(200, content=DOCX, headers={"content-type": MIME_TYPES["docx"]})
        )
        self.failure = failure
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return self.response

    async def download(self, **overrides):
        params = {
            "url": URL,
            "filename": "销售方案.docx",
            "media_type": MIME_TYPES["docx"],
            "expected_format": "docx",
            "allowed_origins": [ORIGIN],
            "transport": httpx.MockTransport(self.handle),
        }
        params.update(overrides)
        return await download_knowledge_artifact(**params)


@pytest.mark.asyncio
async def test_signed_download_preserves_original_and_never_inherits_credentials(monkeypatch, caplog):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER_API_KEY", "do-not-forward")
    network = Network()
    with caplog.at_level(logging.DEBUG):
        result = await network.download()
    assert result.content == DOCX
    assert result.media_type == MIME_TYPES["docx"]
    assert result.filename == "销售方案.docx"
    assert len(network.requests) == 1
    request = network.requests[0]
    assert str(request.url) == URL
    assert request.method == "GET"
    assert set(request.headers) == {"host", "accept", "accept-encoding"}
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["timeout"] == dict.fromkeys(["connect", "read", "write", "pool"], 30)
    assert "private-signature" not in caplog.text
    assert "do-not-forward" not in caplog.text


@pytest.mark.asyncio
async def test_default_transport_disables_environment_proxy_and_retry(monkeypatch):
    network = Network()
    seen_options = []

    def factory(**options):
        seen_options.append(options)
        return httpx.MockTransport(network.handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    result = await network.download(transport=None)
    assert result.content == DOCX
    assert seen_options == [{"trust_env": False, "retries": 0}]
    assert len(network.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_type", [MIME_TYPES["docx"], "application/octet-stream"])
@pytest.mark.parametrize("response_type", [MIME_TYPES["docx"], "application/octet-stream"])
async def test_valid_content_types_normalize_to_verified_concrete_mime(metadata_type, response_type):
    network = Network(httpx.Response(200, content=DOCX, headers={"content-type": response_type + "; charset=binary"}))
    result = await network.download(media_type=metadata_type)
    assert result.content == DOCX
    assert result.media_type == MIME_TYPES["docx"]


@pytest.mark.asyncio
async def test_pptx_is_independently_validated():
    data = package(pptx())
    network = Network(httpx.Response(200, content=data, headers={"content-type": MIME_TYPES["pptx"]}))
    result = await network.download(filename="课件.pptx", media_type=MIME_TYPES["pptx"], expected_format="pptx")
    assert result.content == data
    assert result.slide_count == 1
    assert result.editable_text_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.test:8443/file.docx",
        "file:///etc/passwd",
        "https://files.example.test:8443/file.docx#fragment",
        "https://files.example.test:8443/file.docx#",
        "https://user:password@files.example.test:8443/file.docx",
        "https://@files.example.test:8443/file.docx",
        "https://files.example.test:8443.evil.test/file.docx",
        "https://files.example.test.evil.test:8443/file.docx",
        "https://child.files.example.test:8443/file.docx",
        "https://files.example.test/file.docx",
        "https://files.example.test:443/file.docx",
        "https://127.0.0.1:8443/file.docx",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]:8443/file.docx",
        "https://files.example.test.:8443/file.docx",
        "https://%66iles.example.test:8443/file.docx",
        "https://files.example.test:8443\\@evil.test/file.docx",
        " https://files.example.test:8443/file.docx",
        "https://files.example.test:8443/\nfile.docx",
        "https://files.example.test:65536/file.docx",
        "https://files.example.test:0/file.docx",
        "https://[fe80::1%25en0]/file.docx",
    ],
)
async def test_disallowed_url_never_requests_anything(url):
    network = Network()
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download(url=url)
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_URL_NOT_ALLOWED"
    assert network.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://files.example.test:8443",
        "https://*.example.test",
        "https://files.example.test:8443/path",
        "https://files.example.test:8443?secret=value",
        "https://files.example.test:8443#",
        "https://user@files.example.test:8443",
        "https://files.example.test:8443?",
        "not-an-origin",
    ],
)
async def test_bad_config_cannot_broaden_allowed_origin(origin):
    network = Network()
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download(allowed_origins=[ORIGIN, origin])
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_CONFIGURATION"
    assert not network.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://10.1.2.3:9443", "https://[2001:db8::1]:9443"])
async def test_admin_can_explicitly_allow_fixed_provider_ip(origin):
    network = Network()
    result = await network.download(url=origin + "/file", allowed_origins=[origin])
    assert result.content == DOCX
    assert len(network.requests) == 1


@pytest.mark.asyncio
async def test_default_https_port_and_host_case_are_same_origin():
    network = Network()
    result = await network.download(
        url="https://FILES.EXAMPLE.TEST:443/file", allowed_origins=["https://files.example.test/"]
    )
    assert result.content == DOCX


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"filename": "document.pptx"}, "METADATA"),
        ({"filename": "../document.docx"}, "METADATA"),
        ({"filename": "x\r\n.docx"}, "METADATA"),
        ({"expected_format": "pdf"}, "METADATA"),
        ({"media_type": "text/html"}, "MEDIA_TYPE"),
        ({"media_type": MIME_TYPES["pptx"]}, "MEDIA_TYPE"),
        ({"allowed_origins": []}, "CONFIGURATION"),
        ({"allowed_origins": ORIGIN}, "CONFIGURATION"),
        ({"timeout_seconds": 0}, "CONFIGURATION"),
        ({"timeout_seconds": -1}, "CONFIGURATION"),
        ({"timeout_seconds": True}, "CONFIGURATION"),
        ({"timeout_seconds": float("nan")}, "CONFIGURATION"),
        ({"timeout_seconds": float("inf")}, "CONFIGURATION"),
        ({"timeout_seconds": 121}, "CONFIGURATION"),
    ],
)
async def test_invalid_metadata_and_settings_fail_before_network(overrides, code):
    network = Network()
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download(**overrides)
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_" + code
    assert not network.requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,location",
    [
        (301, ORIGIN + "/other"),
        (302, "https://127.0.0.1/private"),
        (307, "https://evil.test/file"),
        (308, "http://files.example.test/file"),
        (303, "/another-file"),
    ],
)
async def test_no_redirect_is_followed_or_response_body_read(status, location):
    stream = Chunks([b"sensitive redirect body"])
    network = Network(httpx.Response(status, headers={"location": location}, stream=stream))
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download()
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_REDIRECT"
    assert len(network.requests) == 1
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers,code",
    [
        (403, {}, "HTTP_STATUS"),
        (500, {}, "HTTP_STATUS"),
        (204, {}, "HTTP_STATUS"),
        (206, {}, "HTTP_STATUS"),
        (200, {}, "MEDIA_TYPE"),
        (200, {"content-type": "text/html"}, "MEDIA_TYPE"),
        (200, {"content-type": MIME_TYPES["pptx"]}, "MEDIA_TYPE"),
        (200, [("content-type", MIME_TYPES["docx"]), ("content-type", MIME_TYPES["docx"])], "MEDIA_TYPE"),
        (200, {"content-type": MIME_TYPES["docx"], "content-encoding": "gzip"}, "CONTENT_ENCODING"),
        (200, {"content-type": MIME_TYPES["docx"], "transfer-encoding": "gzip, chunked"}, "CONTENT_ENCODING"),
        (200, {"content-type": MIME_TYPES["docx"], "content-range": "bytes 0-9/20"}, "CONTENT_LENGTH"),
        (200, {"content-type": MIME_TYPES["docx"], "content-length": "-1"}, "CONTENT_LENGTH"),
        (200, {"content-type": MIME_TYPES["docx"], "content-length": "1.0"}, "CONTENT_LENGTH"),
        (200, {"content-type": MIME_TYPES["docx"], "content-length": "1, 1"}, "CONTENT_LENGTH"),
        (200, {"content-type": MIME_TYPES["docx"], "content-length": "999999999"}, "TOO_LARGE"),
        (
            200,
            {"content-type": MIME_TYPES["docx"], "content-length": "1", "transfer-encoding": "chunked"},
            "CONTENT_LENGTH",
        ),
        (
            200,
            [("content-type", MIME_TYPES["docx"]), ("content-length", "1"), ("content-length", "1")],
            "CONTENT_LENGTH",
        ),
    ],
)
async def test_response_headers_fail_without_body_or_extra_request(status, headers, code):
    stream = Chunks([DOCX])
    network = Network(httpx.Response(status, headers=headers, stream=stream))
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download()
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_" + code
    assert len(network.requests) == 1
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_chunked_without_length_is_bounded_and_original_bytes_preserved():
    stream = Chunks([DOCX[:21], DOCX[21:]])
    network = Network(
        httpx.Response(200, headers={"content-type": MIME_TYPES["docx"], "transfer-encoding": "chunked"}, stream=stream)
    )
    result = await network.download(limits=replace(ArtifactLimits(), max_bytes=len(DOCX)))
    assert result.content == DOCX
    assert stream.closed


@pytest.mark.asyncio
async def test_oversized_stream_stops_before_remaining_chunks():
    stream = Chunks([b"x" * 50, b"x" * 51, b"never read"])
    network = Network(httpx.Response(200, headers={"content-type": MIME_TYPES["docx"]}, stream=stream))
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download(limits=replace(ArtifactLimits(), max_bytes=100))
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_TOO_LARGE"
    assert len(network.requests) == 1
    assert stream.reads == 2
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [len(DOCX) - 1, len(DOCX) + 1, 0])
async def test_length_mismatch_rejected(declared):
    stream = Chunks([DOCX])
    network = Network(
        httpx.Response(
            200, headers={"content-type": MIME_TYPES["docx"], "content-length": str(declared)}, stream=stream
        )
    )
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download()
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_CONTENT_LENGTH"
    assert stream.closed
    assert len(network.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError("signed URL: " + URL), "NETWORK"),
        (httpx.RemoteProtocolError("body containing " + URL), "NETWORK"),
        (httpx.ConnectTimeout(URL), "TIMEOUT"),
        (httpx.ReadTimeout(URL), "TIMEOUT"),
    ],
)
async def test_network_exceptions_are_sanitized_and_not_retried(failure, code):
    network = Network(failure=failure)
    with pytest.raises(KnowledgeArtifactDownloadError) as error:
        await network.download()
    assert error.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_" + code
    assert "private-signature" not in str(error.value)
    assert "private-signature" not in error.value.message
    assert error.value.__suppress_context__
    assert error.value.__cause__ is None
    assert len(network.requests) == 1


@pytest.mark.asyncio
async def test_network_stream_failure_closes_body_without_retry():
    stream = Chunks([DOCX[:10]], failure=httpx.ReadError(URL))
    network = Network(httpx.Response(200, headers={"content-type": MIME_TYPES["docx"]}, stream=stream))
    with pytest.raises(KnowledgeArtifactDownloadError, match="_NETWORK"):
        await network.download()
    assert stream.closed
    assert len(network.requests) == 1


@pytest.mark.asyncio
async def test_total_timeout_stops_slow_trickle_even_when_each_chunk_arrives():
    stream = Chunks([DOCX[:5]] * 30, delay=0.01)
    network = Network(httpx.Response(200, headers={"content-type": MIME_TYPES["docx"]}, stream=stream))
    with pytest.raises(KnowledgeArtifactDownloadError, match="_TIMEOUT"):
        await network.download(timeout_seconds=0.04)
    assert 1 <= stream.reads < 30
    assert stream.closed
    assert len(network.requests) == 1


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_closes_stream():
    stream = Chunks([], failure=asyncio.CancelledError())
    network = Network(httpx.Response(200, headers={"content-type": MIME_TYPES["docx"]}, stream=stream))
    with pytest.raises(asyncio.CancelledError):
        await network.download()
    assert stream.closed
    assert len(network.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<html>not an Office artifact</html>", package(pptx()), b""])
async def test_claimed_type_does_not_bypass_real_ooxml_validation(body):
    network = Network(httpx.Response(200, content=body, headers={"content-type": "application/octet-stream"}))
    with pytest.raises(KnowledgeArtifactError):
        await network.download()
    assert len(network.requests) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 500])
@pytest.mark.asyncio
async def test_http_failure_exposes_only_safe_status_metadata(status, caplog):
    network = Network(httpx.Response(status, content=b"private-provider-body"))
    with caplog.at_level(logging.DEBUG), pytest.raises(KnowledgeArtifactDownloadError) as exc:
        await network.download()
    assert exc.value.status_code == status
    assert exc.value.code == "KNOWLEDGE_ARTIFACT_DOWNLOAD_HTTP_STATUS"
    assert "private-provider-body" not in str(exc.value) + exc.value.message + caplog.text
    assert "private-signature" not in str(exc.value) + exc.value.message + caplog.text
    assert len(network.requests) == 1
