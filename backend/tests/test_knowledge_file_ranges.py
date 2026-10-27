import hashlib

import pytest

from sales_backend.api.file_ranges import authorized_file_response

CONTENT = b"0123456789"
ETAG = '"' + hashlib.sha256(CONTENT).hexdigest() + '"'


def respond(**kwargs):
    return authorized_file_response(
        CONTENT, media_type="application/pdf", headers={"Cache-Control": "no-store"}, **kwargs
    )


@pytest.mark.parametrize(
    "value,start,end",
    [("bytes=0-2", 0, 2), ("bytes=7-99", 7, 9), ("bytes=6-", 6, 9), ("bytes=-3", 7, 9), ("bytes=-99", 0, 9)],
)
def test_exact_byte_ranges(value, start, end):
    response = respond(range_header=value)
    assert response.status_code == 206
    assert response.body == CONTENT[start : end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/10"
    assert response.headers["content-length"] == str(end - start + 1)
    assert response.headers["etag"] == ETAG
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "value",
    [
        "bytes=10-",
        "bytes=5-2",
        "bytes=-0",
        "bytes=-",
        "bytes=0-1,4-5",
        "items=0-1",
        "bytes=1.1-2",
        "bytes=" + "9" * 300 + "-",
    ],
)
def test_invalid_or_multiple_ranges_return_no_bytes(value):
    response = respond(range_header=value)
    assert response.status_code == 416
    assert response.body == b""
    assert response.headers["content-range"] == "bytes */10"


def test_revision_guard_and_legacy_whole_response():
    response = respond()
    assert response.status_code == 200 and response.body == CONTENT
    assert respond(if_match=ETAG, range_header="bytes=1-2").body == b"12"
    assert respond(if_match='"stale"', range_header="bytes=1-2").status_code == 412
    assert respond(if_match="W/" + ETAG).body == b""
