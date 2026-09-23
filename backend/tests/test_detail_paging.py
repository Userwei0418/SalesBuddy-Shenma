from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sales_backend.domain.detail_paging import read_visit_cursor, visit_cursor


def test_visit_position_round_trips_without_granting_subject_access():
    cid, oid, row_id = uuid4(), uuid4(), uuid4()
    for at in [None, datetime(2026, 9, 1, tzinfo=UTC)]:
        value = visit_cursor({"id": row_id, "interaction_at": at, "created_date": "2026-09-01"}, cid, oid)
        result = read_visit_cursor(value, str(cid), str(oid))
        assert result.id == row_id and result.interaction_at == at
        with pytest.raises(ValueError):
            read_visit_cursor(value, uuid4(), oid)
        with pytest.raises(ValueError):
            read_visit_cursor(value, cid, None)


@pytest.mark.parametrize("value", ["%%%", "e30", "a" * 1025, "bm90IGpzb24", "eyJjdXN0b21lcl9pZCI6ImJhZCJ9"])
def test_invalid_visit_positions_are_rejected(value):
    with pytest.raises(ValueError, match="分页位置无效"):
        read_visit_cursor(value, uuid4(), None)


def test_entry_time_cursor_binds_sort_subject_and_full_timestamp():
    cid, oid, row_id = uuid4(), uuid4(), uuid4()
    at = datetime(2026, 9, 16, 7, 21, 3, 456789, tzinfo=UTC)
    value = visit_cursor({"id": row_id, "created_at": at}, cid, oid, sort="created_desc")
    position = read_visit_cursor(value, cid, oid, sort="created_desc")
    assert position.created_at == at and position.id == row_id
    for customer, opportunity, sort in [(cid, oid, None), (uuid4(), oid, "created_desc"), (cid, None, "created_desc")]:
        with pytest.raises(ValueError):
            read_visit_cursor(value, customer, opportunity, sort=sort)
    legacy = visit_cursor({"id": row_id, "interaction_at": at, "created_date": "2026-09-01"}, cid, oid)
    with pytest.raises(ValueError):
        read_visit_cursor(legacy, cid, oid, sort="created_desc")
