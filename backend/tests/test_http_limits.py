from types import SimpleNamespace

import pytest

from sales_backend.services.http_limits import request_bucket, TenantRateLimited


@pytest.mark.parametrize('method,path,endpoint,expected', [
    ('GET', '/api/v1/console/organization', ('operations_accounts','organization'), 'read'),
    ('POST', '/api/v1/future', ('future','new_action'), 'write'),
    ('POST', '/api/v1/audio/transcriptions', ('audio','transcribe_audio'), 'upload'),
    ('POST', '/api/v1/visit-imports', ('visit_imports','upload_visit_file'), 'upload'),
    ('POST', '/api/v1/visit-imports/uuid/retry', ('visit_imports','retry_import'), 'write'),
    ('GET', '/api/v1/console/auth/me', ('console_auth','me'), None),
    ('POST', '/api/v1/console/auth/password', ('console_auth','change_password'), None),
    ('POST', '/api/v1/auth/logout', ('auth','logout'), None),
])
def test_server_routes_choose_bucket_not_caller_headers(method, path, endpoint, expected):
    handler = SimpleNamespace(__module__='sales_backend.api.'+endpoint[0], __name__=endpoint[1])
    request = SimpleNamespace(method=method, url=SimpleNamespace(path=path), scope={'endpoint':handler},
                              headers={'Content-Type':'multipart/form-data','X-Bucket':'read','X-Company-ID':'forged'})
    assert request_bucket(request) == expected


def test_rejection_always_has_positive_retry_time():
    assert TenantRateLimited(0).retry_after == 1
