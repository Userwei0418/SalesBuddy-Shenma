"""Run genuine COMMIT fault tests only inside the self-created CI database suite."""
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_actual_commit_upload_worker_and_archive_lifecycle(connection):
    if not str(await connection.fetchval("SELECT current_database()")).startswith("salegent_verify_integration_"):
        pytest.skip("Commit fault tests require the disposable PostgreSQL system runner")
    path = str(Path(__file__).resolve().parents[1] / "system")
    sys.path.insert(0, path)
    try:
        from verify_upload_lifecycle_postgres import main
        await main()
    finally:
        sys.path.remove(path)
