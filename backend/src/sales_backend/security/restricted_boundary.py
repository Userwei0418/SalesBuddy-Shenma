"""Reject purpose-bound credentials before body parsing, uploads or route work.

The signed audience identifies restricted credentials; client channel headers,
roles and JSON session_kind fields are never authorization evidence.
"""
import re

from sales_backend.auth.restricted import RestrictedTokens
from sales_backend.auth.tokens import TokenError
from sales_backend.error_contract import error_response

READ_PATH = re.compile(r"/api/v1/demo-view/(?:session|overview|(?:customers|opportunities|follow-ups|tasks)(?:/[0-9a-fA-F-]{36})?)\Z")


async def restricted_boundary(request, call_next):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            RestrictedTokens(request.app.state.settings).decode(token, verify_exp=False)
        except TokenError:
            pass  # Ordinary/invalid tokens still face each endpoint's normal gate.
        else:
            path = request.url.path.rstrip("/")
            permitted = (request.method == "GET" and READ_PATH.fullmatch(path)) or (
                request.method == "POST" and path in {
                    "/api/v1/demo-view/logout", "/api/v1/auth/password/login",
                    "/api/v1/console/auth/login",  # Independent credentials; cannot authorize Web.
                }
            )
            if not permitted:
                return error_response(request, status_code=403, code="RESTRICTED_ENDPOINT_DENIED")
    return await call_next(request)
