"""Exercise the customer subpath, shared API discovery and release cache policy."""
import re

import pytest
from fastapi.testclient import TestClient

from sales_backend.main import app, BUSINESS_WEB_DIST


@pytest.mark.skipif(not BUSINESS_WEB_DIST.is_dir(), reason="Build business-web before static acceptance")
def test_workspace_serves_complete_relative_entry_and_revalidates():
    client = TestClient(app)
    entry = client.get('/workspace/')
    assert entry.status_code == 200
    assert entry.headers['cache-control'] == 'no-cache, must-revalidate'
    for asset in re.findall(r'(?:src|href)="([^"#]+)"', entry.text):
        if asset.startswith(('/', 'http')):
            continue
        response = client.get('/workspace/' + asset)
        assert response.status_code == 200, asset
        assert response.headers['cache-control'] == 'no-cache, must-revalidate'
        cached = client.get('/workspace/' + asset, headers={'If-None-Match':response.headers['etag']})
        assert cached.status_code == 304
    assert client.get('/workspace/../backend/.env').status_code == 404
    assert client.get('/admin').status_code == 200


def test_web_boot_discovery_does_not_expose_runtime_credentials(monkeypatch):
    from sales_backend import main
    async def readiness(response):
        return {'status':'ok', 'checks':{'database':True}}
    monkeypatch.setattr(main, 'health_ready', readiness)
    client = TestClient(app)
    assert client.get('/web-capabilities').json() == {'previewOnly':False}
    response = client.get('/connection-status')
    assert response.status_code == 200
    assert response.json()['apiPrefix'] == '/api/v1'
    assert response.json()['localQuickLogin'] == {'available':False}
    assert response.json()['reachable'] is True
