"""Configurable originals storage. Every file carries its own location, never the current default.

PostgreSQL chunks and their import/job commit together. S3 upload runs outside the
DB transaction. Uncertain registrations retain their object for reconciliation.
Profiles in use must remain configured until historical migration is verified.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from sales_backend.async_resources import file_io

CHUNK_SIZE = 1024 * 1024


class MaterialStorage:
    def __init__(self, config=None):
        if config is None:
            path = os.environ.get("VISIT_STORAGE_CONFIG_FILE")
            config = (
                json.loads(Path(path).read_text())
                if path
                else {"default": "postgres", "profiles": {"postgres": {"driver": "postgres"}}}
            )
        self.default = config["default"]
        self.profiles = dict(config["profiles"])
        self.profiles.setdefault(
            "legacy-local",
            {"driver": "local", "root": os.environ.get("VISIT_IMPORT_ROOT", "/var/lib/sales-backend/visit-imports")},
        )
        if self.profile(self.default)["driver"] == "local":
            raise ValueError("本地文件仅用于读取历史材料；新材料请选择postgres或s3")

    def profile(self, name, driver=None):
        value = self.profiles.get(name)
        if not value or value.get("driver") not in {"postgres", "local", "s3"}:
            raise ValueError("文件存储配置不存在或格式不正确")
        if driver and value["driver"] != driver:
            raise ValueError("历史文件存储配置不能改变类型，请新建配置名称")
        return value

    @staticmethod
    def _client(profile):
        from aiobotocore.config import AioConfig
        from aiobotocore.session import get_session

        return get_session().create_client(
            "s3",
            endpoint_url=profile.get("endpoint_url"),
            region_name=profile.get("region", "us-east-1"),
            config=AioConfig(
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 2},
                s3={"addressing_style": profile.get("addressing_style", "auto")},
            ),
        )

    async def prepare(self, actor, upload):
        profile = self.profile(self.default)
        digest = await file_io(self._hash, upload.path)
        key = str(upload.path) if profile["driver"] == "local" else f"{actor.workspace_id}/{upload.import_id}"
        if profile["driver"] == "s3":
            key = profile.get("prefix", "visits").strip("/") + "/" + key
            await self._upload_s3(profile, upload.path, key)
        return {"profile": self.default, "driver": profile["driver"], "key": key, "sha256": digest}

    @staticmethod
    def _hash(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    async def _upload_s3(self, profile, path, key):
        # Bounded originals (upload limit 100 MiB); network cancellation closes the client.
        content = await file_io(path.read_bytes)
        async with asyncio.timeout(120), self._client(profile) as client:
            await client.put_object(Bucket=profile["bucket"], Key=key, Body=content)

    async def register(self, connection, actor, upload, location):
        await connection.execute(
            """UPDATE activity.visit_import SET storage_profile=$2,storage_driver=$3,storage_key=$4,
            content_sha256=$5,file_path=$6 WHERE id=$1::uuid""",
            upload.import_id,
            location["profile"],
            location["driver"],
            location["key"],
            location["sha256"],
            str(upload.path) if location["driver"] == "local" else "",
        )
        if location["driver"] == "postgres":
            with upload.path.open("rb") as stream:
                number = 0
                while chunk := await file_io(stream.read, CHUNK_SIZE):
                    await connection.execute(
                        "INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) "
                        "VALUES($1::uuid,$2::uuid,$3,$4)",
                        upload.import_id,
                        actor.workspace_id,
                        number,
                        chunk,
                    )
                    number += 1

    async def read(self, database, actor, row):
        profile = self.profile(row.get("storage_profile", "legacy-local"), row.get("storage_driver", "local"))
        key = row.get("storage_key") or row["file_path"]
        if not 0 < row["file_size"] <= 100 * CHUNK_SIZE:
            raise ValueError("文件大小不正确")
        if profile["driver"] == "postgres":
            async with database.transaction(actor, readonly=True) as connection:
                # Ordered chunks avoid fetching binary content in ordinary list/detail queries.
                chunks = bytearray()
                index = 0
                async for part in connection.cursor(
                    "SELECT chunk_no,content FROM activity.visit_import_content "
                    "WHERE import_id=$1::uuid ORDER BY chunk_no",
                    str(row["id"]),
                    prefetch=4,
                ):
                    if part["chunk_no"] != index or len(chunks) + len(part["content"]) > row["file_size"]:
                        raise ValueError("原始文件不完整，请联系运营")
                    chunks.extend(part["content"])
                    index += 1
                content = bytes(chunks)
        elif profile["driver"] == "local":
            path = Path(key)
            if path.resolve().parent != Path(profile["root"]).resolve():
                raise ValueError("文件路径不正确")
            if path.stat().st_size != row["file_size"]:
                raise ValueError("原始文件大小不匹配")
            content = await file_io(path.read_bytes)
        else:
            content = await self._read_s3(profile, key, row["file_size"])
        if len(content) != row["file_size"] or (
            row.get("content_sha256") and hashlib.sha256(content).hexdigest() != row["content_sha256"]
        ):
            raise ValueError("原始文件完整性校验失败")
        return content

    async def _read_s3(self, profile, key, expected_size):
        async with asyncio.timeout(120), self._client(profile) as client:
            response = await client.get_object(Bucket=profile["bucket"], Key=key)
            async with response["Body"] as stream:
                if response["ContentLength"] != expected_size:
                    raise ValueError("原始文件大小不匹配")
                content = bytearray()
                while chunk := await stream.read(CHUNK_SIZE):
                    content.extend(chunk)
                    if len(content) > expected_size:
                        raise ValueError("原始文件超过登记大小")
                return bytes(content)
