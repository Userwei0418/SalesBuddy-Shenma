"""Execute real feature-gate SQL against exact ledgers, not string-only doubles.

SQLite's attached ops database executes the portable EXISTS/IN/min queries; this
checks their semantics without inventing PostgreSQL migrations or touching a
server. Existing HTTP tests separately exercise the guards' caller paths.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories.task_alignment import TaskAlignmentRepository
from sales_backend.repositories.work_report_meeting_facts import _meeting_schema_applied_at
from sales_backend.services.work_report_overview import _require_overview_schema

EARLY = "2026-09-29T01:02:03+00:00"
LATE = "2026-10-15T04:05:06+00:00"


class Ledger:
    def __init__(self, rows):
        self.database = sqlite3.connect(":memory:")
        self.database.execute("ATTACH DATABASE ':memory:' AS ops")
        self.database.execute("CREATE TABLE ops.schema_migration(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        self.database.executemany("INSERT INTO ops.schema_migration VALUES (?,?)", rows.items())

    async def fetchval(self, query):
        return self.database.execute(query).fetchone()[0]

    def close(self):
        self.database.close()


@pytest.fixture
def ledger():
    opened = []

    def create(rows):
        value = Ledger(rows)
        opened.append(value)
        return value

    yield create
    for value in opened:
        value.close()


def test_fixed_mappings_match_real_bridge_manifest():
    manifest = json.loads((Path(__file__).resolve().parents[2] / "database/shenma_whole_121_manifest.json").read_text())
    mappings = {row["source"].split("/")[-1].split("__")[0]:
                row["target"].split("/")[-1].split("__")[0] for row in manifest["migrations"]}
    assert {key: mappings[key] for key in ("V235", "V236", "V244")} == {
        "V235": "V383", "V236": "V384", "V244": "V392",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["V236", "V384"])
async def test_overview_accepts_only_completed_source_or_mapped_feature(ledger, version):
    await _require_overview_schema(ledger({version: EARLY}))


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [{}, {"V235": EARLY}, {"V383": EARLY}, {"V392": EARLY}, {"V399": LATE}])
async def test_overview_missing_exact_feature_keeps_409_even_with_later_version(ledger, rows):
    with pytest.raises(WorkReportError) as error:
        await _require_overview_schema(ledger(rows))
    assert error.value.status == 409 and error.value.code == "WORK_REPORT_INVALID_INPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["V235", "V383"])
async def test_meeting_retains_source_or_mapped_applied_at(ledger, version):
    assert await _meeting_schema_applied_at(ledger({version: EARLY})) == EARLY


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    {"V235": EARLY, "V383": LATE}, {"V235": LATE, "V383": EARLY},
])
async def test_meeting_does_not_reset_first_feature_timestamp(ledger, rows):
    assert await _meeting_schema_applied_at(ledger(rows)) == EARLY


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [{}, {"V236": EARLY}, {"V384": EARLY}, {"V392": EARLY}, {"V399": LATE}])
async def test_meeting_missing_exact_feature_keeps_409(ledger, rows):
    with pytest.raises(WorkReportError) as error:
        await _meeting_schema_applied_at(ledger(rows))
    assert error.value.status == 409 and error.value.code == "WORK_REPORT_INVALID_INPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["V244", "V392"])
async def test_task_alignment_accepts_source_and_mapped_feature(ledger, version):
    assert await TaskAlignmentRepository.supports_v2(ledger({version: EARLY}))


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [{}, {"V235": EARLY}, {"V383": EARLY}, {"V384": EARLY}, {"V399": LATE}])
async def test_task_alignment_rejects_missing_feature_despite_later_version(ledger, rows):
    assert not await TaskAlignmentRepository.supports_v2(ledger(rows))
