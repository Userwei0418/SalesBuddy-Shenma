"""Pin administrator-configured model requests to validated public addresses."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

from sales_backend.domain.model_api import public_endpoint


class PublicModelTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        # No proxy environment, redirects, cookies or ambient credentials.
        self.transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        public_endpoint(str(request.url))
        host, port = request.url.host, request.url.port or 443
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=10
            )
            addresses = list(dict.fromkeys(row[4][0] for row in records))
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise ValueError()
        except (OSError, ValueError, TimeoutError):
            raise httpx.ConnectError("模型接口未解析到可用公网地址") from None
        # The connection uses this address, not a second DNS lookup. TLS still
        # verifies the original hostname and HTTP Host preserves virtual hosting.
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await self.transport.handle_async_request(pinned)

    async def aclose(self):
        await self.transport.aclose()
