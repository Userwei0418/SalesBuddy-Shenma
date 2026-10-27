"""Shared timeout recovery protocol, including ambiguous mutation outcomes."""


def install_recovery_openapi(app):
    original = app.openapi

    def openapi():
        schema = original()
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {})["TransientRecoveryError"] = {
            "type": "object",
            "required": ["code", "detail", "request_id", "retryable", "recovery_action"],
            "properties": {
                "code": {"type": "string", "enum": ["DATABASE_BUSY", "REQUEST_TIMEOUT"]},
                "detail": {"type": "string"},
                "request_id": {"type": "string"},
                "retryable": {"type": "boolean", "description": "True only for GET/HEAD; never permission to replay a mutation."},
                "recovery_action": {"type": "string", "enum": ["retry_read", "check_status"]},
            },
        }
        responses = components.setdefault("responses", {})
        for status, name, meaning in (
            ("503", "DatabaseBusyRecovery", "Database connection pool wait budget exceeded."),
            ("504", "RequestTimeoutRecovery", "Database command or bounded business read timed out."),
        ):
            responses[name] = {
                "description": meaning + " GET/HEAD may retry after 5 seconds. Mutations have an unknown outcome: check existing receipts/status first; never automatically replay. Cache-Control: no-store.",
                "headers": {
                    "Retry-After": {"description": "Only GET/HEAD responses include this header.", "schema": {"type": "string", "enum": ["5"]}},
                    "X-Request-Id": {"schema": {"type": "string"}},
                },
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TransientRecoveryError"}}},
            }
            for path, methods in schema["paths"].items():
                if not path.startswith("/api/") or path.startswith("/api/v1/health/"):
                    continue
                for method, operation in methods.items():
                    if method in {"get", "head", "post", "put", "patch", "delete"}:
                        operation.setdefault("responses", {}).setdefault(status, {"$ref": f"#/components/responses/{name}"})
        return schema

    app.openapi = openapi
