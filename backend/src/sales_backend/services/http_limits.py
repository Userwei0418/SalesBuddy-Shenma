"""Admission is committed before business work, using only a verified identity."""
from sales_backend.db import json_value


RECOVERY_PATHS = frozenset({
    '/api/v1/auth/me', '/api/v1/auth/password', '/api/v1/auth/logout',
    '/api/v1/console/auth/me', '/api/v1/console/auth/password', '/api/v1/console/auth/logout',
})
UPLOAD_ENDPOINTS = frozenset({('audio', 'transcribe_audio'), ('visit_imports', 'upload_visit_file')})


class TenantRateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after))
        super().__init__(f'本公司请求较多，请在 {self.retry_after} 秒后重试')


def request_bucket(request):
    if request.url.path in RECOVERY_PATHS:
        return None
    endpoint = request.scope.get('endpoint')
    key = (getattr(endpoint, '__module__', '').rsplit('.', 1)[-1], getattr(endpoint, '__name__', ''))
    if key in UPLOAD_ENDPOINTS:
        return 'upload'
    return 'read' if request.method in {'GET', 'HEAD', 'OPTIONS'} else 'write'


async def admit_request(request, database, actor):
    bucket = request_bucket(request)
    if bucket is None:
        return
    # Raise outside the transaction: 429 and later business failures must not
    # roll back admission or rejected-request telemetry.
    async with database.transaction(actor) as connection:
        result = json_value(await connection.fetchval('SELECT security.consume_http_request($1)', bucket))
    if not result['allowed']:
        raise TenantRateLimited(result['retry_after'])
