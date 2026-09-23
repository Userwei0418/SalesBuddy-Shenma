import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.material_storage import CHUNK_SIZE, MaterialStorage


class Database:
    def __init__(self, parts):
        self.parts = parts

    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield self

    async def cursor(self, *args, **kwargs):
        for i, part in enumerate(self.parts):
            yield {"chunk_no": i, "content": part}


@pytest.mark.asyncio
async def test_postgres_roundtrip_and_config_switch(tmp_path):
    content = b"a" * CHUNK_SIZE + b"\xe4\xb8\xad\xe6\x96\x87"
    path = tmp_path / "original.txt"
    path.write_bytes(content)
    upload = SimpleNamespace(path=path, import_id="import")
    actor = SimpleNamespace(workspace_id="workspace")
    storage = MaterialStorage()
    location = await storage.prepare(actor, upload)
    assert location["driver"] == "postgres"
    connection = SimpleNamespace(execute=AsyncMock())
    await storage.register(connection, actor, upload, location)
    chunks = [c.args[-1] for c in connection.execute.await_args_list[1:]]
    assert len(chunks) == 2
    # Changed default does not reroute an already registered file.
    changed = MaterialStorage(
        {
            "default": "new",
            "profiles": {"postgres": {"driver": "postgres"}, "new": {"driver": "s3", "bucket": "future"}},
        }
    )
    row = {
        "id": "import",
        "file_size": len(content),
        "storage_profile": "postgres",
        "storage_driver": "postgres",
        "storage_key": "workspace/import",
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }
    assert await changed.read(Database(chunks), actor, row) == content
    with pytest.raises(ValueError):
        await changed.read(Database(chunks[:-1]), actor, row)
    row["content_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        await changed.read(Database(chunks), actor, row)


@pytest.mark.asyncio
async def test_legacy_original_still_readable(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes(b"old")
    storage = MaterialStorage(
        {
            "default": "postgres",
            "profiles": {
                "postgres": {"driver": "postgres"},
                "legacy-local": {"driver": "local", "root": str(tmp_path)},
            },
        }
    )
    row = {"id": "old", "file_size": 3, "file_path": str(path)}
    assert await storage.read(None, None, row) == b"old"
    row["file_path"] = str(tmp_path.parent / "escape.txt")
    with pytest.raises(ValueError):
        await storage.read(None, None, row)


def test_used_profile_cannot_change_type():
    storage = MaterialStorage()
    with pytest.raises(ValueError):
        storage.profile("postgres", "s3")
