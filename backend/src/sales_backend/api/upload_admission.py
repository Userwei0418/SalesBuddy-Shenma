"""Bound multipart reception before FastAPI parses/spools an upload body.

Slots are process-local, not a deployment-wide disk or tenant storage quota.
Business file limits and uncertain-commit original retention remain in the service.
"""

import asyncio
import math
import os
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from threading import Lock

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile

from sales_backend.api.dependencies import bearer, get_identity
from sales_backend.api.live_identity import live_identity_connection
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.visit_import import MAX_UPLOAD


@dataclass(frozen=True)
class UploadLimits:
    max_active: int = 4
    tenant_active: int = 2
    receive_seconds: float = 120
    idle_seconds: float = 30
    request_bytes: int = MAX_UPLOAD + 64 * 1024
    field_bytes: int = 4096

    @classmethod
    def configured(cls):
        try:
            maximum = int(os.environ.get("SALES_UPLOAD_MAX_ACTIVE", "4"))
            tenant = int(os.environ.get("SALES_UPLOAD_TENANT_ACTIVE", "2"))
            total = float(os.environ.get("SALES_UPLOAD_RECEIVE_SECONDS", "120"))
            idle = float(os.environ.get("SALES_UPLOAD_IDLE_SECONDS", "30"))
            if not (1 <= tenant <= maximum <= 32 and math.isfinite(total) and math.isfinite(idle)
                    and 1 <= idle <= total <= 1800):
                raise ValueError
        except ValueError:
            raise HTTPException(503, "上传接收配置无效，请联系管理员") from None
        return cls(maximum, tenant, total, idle)


class UploadSlots:
    def __init__(self):
        self._lock = Lock()
        self._total = 0
        self._tenants = Counter()

    @contextmanager
    def claim(self, workspace, limits):
        with self._lock:
            if self._total >= limits.max_active or self._tenants[workspace] >= limits.tenant_active:
                raise HTTPException(429, "上传服务繁忙，请稍后重试", headers={"Retry-After": "5"})
            self._total += 1
            self._tenants[workspace] += 1
        try:
            yield
        finally:
            with self._lock:
                self._total -= 1
                self._tenants[workspace] -= 1
                if not self._tenants[workspace]:
                    del self._tenants[workspace]


_slots = UploadSlots()


async def _authorize_upload(request):
    database = request.app.state.database
    identity = await get_identity(request, await bearer(request), database, request.app.state.settings)
    async with database.transaction(identity.actor, readonly=True) as connection:
        effective = await AuthorizationRepository().effective(connection)
    if identity.client_channel == "web":
        if identity.auth_method != "password":
            raise PermissionError("请使用正式账号密码登录")
        effective.require("access.console")
    else:
        effective.require("access.mini_program")
    effective.require("visit.upload")
    return identity


async def _revalidate_upload(request, identity):
    # Slow reception must not turn an old verified_identity cache into authority.
    async with live_identity_connection(request, identity, request.app.state.database) as (_, effective):
        effective.require("visit.upload")


def _check_headers(request, limits):
    message = Message()
    message["content-type"] = request.headers.get("content-type", "")
    if message.get_content_type() != "multipart/form-data":
        raise HTTPException(415, "请使用文件上传方式提交材料")
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,12}", lengths[0])):
        raise HTTPException(400, "上传长度无效")
    if lengths and int(lengths[0]) > limits.request_bytes:
        raise HTTPException(413, "文档最大20MB，录音最大100MB")


def _validate_form(form):
    if (any(key not in {"file", "original_filename"} for key, _ in form.multi_items())
            or len(form.getlist("file")) != 1 or not isinstance(form["file"], UploadFile)
            or len(form.getlist("original_filename")) > 1
            or not isinstance(form.get("original_filename", ""), str)):
        raise HTTPException(422, "请仅上传一个文件及可选的原文件名")


class VisitUploadRoute(APIRoute):
    def get_route_handler(self):
        downstream = super().get_route_handler()

        async def admitted(request: Request):
            limits = UploadLimits.configured()
            _check_headers(request, limits)
            identity = await _authorize_upload(request)
            with _slots.claim(identity.actor.workspace_id, limits):
                received = 0

                async def receive():
                    nonlocal received
                    try:
                        async with asyncio.timeout(limits.idle_seconds):
                            event = await request.receive()
                    except TimeoutError:
                        raise HTTPException(408, "上传接收超时，请检查网络后重试") from None
                    if event["type"] == "http.request":
                        received += len(event.get("body", b""))
                        if received > limits.request_bytes:
                            raise HTTPException(413, "文档最大20MB，录音最大100MB")
                    return event

                # Same scope/state, but every body byte must pass the bounded receive.
                # FastAPI receives this exact Request, reusing its parsed FormData.
                limited = Request(request.scope, receive=receive)
                form = None
                try:
                    try:
                        async with asyncio.timeout(limits.receive_seconds):
                            form = await limited.form(max_files=1, max_fields=1, max_part_size=limits.field_bytes)
                    except TimeoutError:
                        raise HTTPException(408, "上传接收超时，请检查网络后重试") from None
                    _validate_form(form)
                    await _revalidate_upload(limited, identity)
                    return await downstream(limited)
                finally:
                    if form is not None:
                        await form.close()

        return admitted
