"""Receive an explicitly described provider file from a trusted HTTPS origin.

Origin configuration is administrator-owned, never derived from a provider's
response. This module does not parse provider envelopes, inherit API credentials,
follow redirects, retry, or rewrite Office bytes. Injected transports are intended
for isolated tests and are closed after this single download.
"""

import asyncio
import ipaddress
import math
import re
from collections.abc import Collection
from urllib.parse import urlsplit

import httpx

from sales_backend.services.knowledge_artifacts import (
    MIME_TYPES,
    ArtifactFormat,
    ArtifactLimits,
    ValidatedKnowledgeArtifact,
    validate_knowledge_artifact,
)

_BINARY_TYPE = "application/octet-stream"
_DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\Z")


class KnowledgeArtifactDownloadError(ValueError):
    """Stable error with no URL, provider body, credentials, or raw exception."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        self.code = "KNOWLEDGE_ARTIFACT_DOWNLOAD_" + code
        self.message = message
        super().__init__(self.code)


def _fail(code: str, message: str):
    raise KnowledgeArtifactDownloadError(code, message)


def _url(value: str, *, origin_only: bool = False) -> httpx.URL:
    code = "CONFIGURATION" if origin_only else "URL_NOT_ALLOWED"
    if (
        not isinstance(value, str)
        or not value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or "\\" in value
        or "#" in value
    ):
        _fail(code, "文件来源地址不符合 HTTPS 来源规则")
    try:
        parts = urlsplit(value)
        parsed = httpx.URL(value)
        # Do not accept URL credentials, encoded host tricks, or IPv6 zone IDs.
        if "@" in parts.netloc or "%" in parts.netloc:
            _fail(code, "文件来源地址不能包含用户信息或编码主机名")
        host = parsed.raw_host.decode("ascii")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not _DNS_NAME.fullmatch(host):
                _fail(code, "文件来源主机名无效")
        if parts.scheme.lower() != "https" or parsed.scheme != "https" or not host:
            _fail(code, "文件来源必须使用 HTTPS")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            _fail(code, "文件来源端口无效")
        if origin_only and (parts.path not in {"", "/"} or "?" in value):
            _fail(code, "允许来源只能配置 HTTPS 主机和端口")
        return parsed
    except (httpx.InvalidURL, UnicodeError, ValueError) as exc:
        if isinstance(exc, KnowledgeArtifactDownloadError):
            raise
        raise KnowledgeArtifactDownloadError(code, "文件来源地址无效") from None


def _origin(url: httpx.URL) -> tuple[str, int]:
    return url.raw_host.decode("ascii"), url.port or 443


def _media_type(value: str, expected: str) -> str:
    if not isinstance(value, str):
        _fail("MEDIA_TYPE", "文件类型缺失或不匹配")
    essence = value.partition(";")[0].strip().lower()
    if essence not in {expected, _BINARY_TYPE}:
        _fail("MEDIA_TYPE", "文件类型与所请求的 Office 格式不匹配")
    return essence


def _response_length(response: httpx.Response, *, expected_mime: str, maximum: int) -> int | None:
    if 300 <= response.status_code < 400:
        _fail("REDIRECT", "文件下载地址发生跳转，请提供已批准来源的直接下载地址")
    if response.status_code != 200:
        raise KnowledgeArtifactDownloadError(
            "HTTP_STATUS", "文件服务未成功返回完整文件", status_code=response.status_code
        )
    media_types = response.headers.get_list("content-type")
    if len(media_types) != 1:
        _fail("MEDIA_TYPE", "文件响应必须包含唯一的文件类型")
    _media_type(media_types[0], expected_mime)
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        _fail("CONTENT_ENCODING", "文件响应不支持额外的 HTTP 压缩编码")
    if "transfer-encoding" in response.headers and response.headers["transfer-encoding"].strip().lower() != "chunked":
        _fail("CONTENT_ENCODING", "文件响应包含不支持的传输编码")
    if "content-range" in response.headers:
        _fail("CONTENT_LENGTH", "文件服务返回了未请求的分段文件")
    lengths = response.headers.get_list("content-length")
    if not lengths:
        return None
    if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0]) or "transfer-encoding" in response.headers:
        _fail("CONTENT_LENGTH", "文件响应长度信息无效或冲突")
    length = int(lengths[0])
    if length > maximum:
        _fail("TOO_LARGE", "生成文件超过允许的下载大小")
    return length


async def download_knowledge_artifact(
    url: str,
    *,
    filename: str,
    media_type: str,
    expected_format: ArtifactFormat,
    allowed_origins: Collection[str],
    limits: ArtifactLimits = ArtifactLimits(),
    timeout_seconds: float = 30,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ValidatedKnowledgeArtifact:
    """Download once, then validate real OOXML, returning its unchanged bytes.

    An explicitly configured fixed IP is permitted (including a private provider
    IP). No host or origin is inferred, and no redirect is ever requested. The
    total network deadline includes all stream reads, not just idle read time.
    """
    if (
        expected_format not in MIME_TYPES
        or not isinstance(filename, str)
        or not filename
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        or not filename.lower().endswith("." + expected_format)
    ):
        _fail("METADATA", "文件名称扩展名与请求格式不匹配")
    expected_mime = MIME_TYPES[expected_format]
    _media_type(media_type, expected_mime)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 120
        or not isinstance(allowed_origins, Collection)
        or isinstance(allowed_origins, (str, bytes))
        or not allowed_origins
    ):
        _fail("CONFIGURATION", "下载来源或超时时间未正确配置")
    origins = {_origin(_url(origin, origin_only=True)) for origin in allowed_origins}
    file_url = _url(url)
    if _origin(file_url) not in origins:
        _fail("URL_NOT_ALLOWED", "文件下载来源不在管理员批准的来源中")

    # The public transport interface deliberately bypasses AsyncClient's auth,
    # cookies, redirects and INFO logging of signed URLs. TLS verification stays
    # enabled; environment proxies and retry behavior are explicitly disabled.
    request = httpx.Request(
        "GET",
        file_url,
        headers={"Accept": f"{expected_mime}, {_BINARY_TYPE}", "Accept-Encoding": "identity"},
        extensions={"timeout": httpx.Timeout(timeout_seconds).as_dict()},
    )
    receiver = transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    content = bytearray()
    try:
        async with asyncio.timeout(timeout_seconds), receiver:
            response = await receiver.handle_async_request(request)
            try:
                declared = _response_length(response, expected_mime=expected_mime, maximum=limits.max_bytes)
                async for chunk in response.aiter_bytes(chunk_size=min(64 * 1024, limits.max_bytes + 1)):
                    if len(content) + len(chunk) > limits.max_bytes:
                        _fail("TOO_LARGE", "生成文件超过允许的下载大小")
                    content.extend(chunk)
                    if declared is not None and len(content) > declared:
                        _fail("CONTENT_LENGTH", "文件实际大小与响应声明不一致")
                if declared is not None and len(content) != declared:
                    _fail("CONTENT_LENGTH", "文件未完整下载或长度不一致")
            finally:
                await response.aclose()
    except (TimeoutError, httpx.TimeoutException):
        raise KnowledgeArtifactDownloadError("TIMEOUT", "文件下载超时，请稍后重新生成或下载") from None
    except httpx.HTTPError:
        raise KnowledgeArtifactDownloadError("NETWORK", "文件下载失败，请检查文件服务状态") from None

    return validate_knowledge_artifact(
        bytes(content), filename=filename, media_type=expected_mime, expected_format=expected_format, limits=limits
    )
