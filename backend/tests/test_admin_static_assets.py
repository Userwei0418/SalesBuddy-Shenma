"""Console releases must revalidate entry files and imported modules alike."""

import pytest
from fastapi.testclient import TestClient

from sales_backend.main import app


@pytest.mark.parametrize("asset", ["console.js", "core.js", "pages.js", "partners.js", "ai-capabilities.js", "agent-audit.js", "console.css"])
def test_console_assets_require_revalidation(asset):
    response = TestClient(app).get(f"/admin/assets/{asset}")

    assert response.status_code == 200
    assert response.content
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert response.headers["etag"]
    assert response.headers["last-modified"]


@pytest.mark.parametrize(
    ("request_header", "response_header"),
    [("If-None-Match", "etag"), ("If-Modified-Since", "last-modified")],
)
def test_not_modified_assets_keep_revalidation_policy(request_header, response_header):
    client = TestClient(app)
    original = client.get("/admin/assets/tasks.js")
    cached = client.get(
        "/admin/assets/tasks.js",
        headers={request_header: original.headers[response_header]},
    )

    assert cached.status_code == 304
    assert not cached.content
    assert cached.headers["etag"] == original.headers["etag"]
    assert cached.headers["cache-control"] == "no-cache, must-revalidate"


def test_previous_release_etag_receives_current_asset():
    client = TestClient(app)
    current = client.get("/admin/assets/console.js")
    refreshed = client.get(
        "/admin/assets/console.js",
        headers={"If-None-Match": '"previous-release"'},
    )

    assert refreshed.status_code == 200
    assert refreshed.content == current.content
    assert refreshed.headers["etag"] == current.headers["etag"]
    assert refreshed.headers["cache-control"] == "no-cache, must-revalidate"
