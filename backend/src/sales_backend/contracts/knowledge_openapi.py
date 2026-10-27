"""Expose the required knowledge write receipt key enforced by the service."""


def install_knowledge_openapi(app):
    original = app.openapi

    def openapi():
        schema = original()
        for path, methods in schema["paths"].items():
            if not path.startswith(("/api/v1/knowledge/", "/api/v1/public/knowledge/")):
                continue
            for method, operation in methods.items():
                if method in {"get", "head", "post", "put", "patch", "delete"}:
                    operation["x-knowledge-availability"] = "temporarily_unavailable"
                    operation.setdefault("responses", {})["503"] = {
                        "description": "KNOWLEDGE_TEMPORARILY_UNAVAILABLE：知识库暂不能使用。请求在执行前拒绝，retryable=false；原数据与权限保持不变。",
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/PublicApiError"},
                        }},
                    }
                if method not in {"post", "put"}:
                    continue
                for parameter in operation.get("parameters", []):
                    if parameter["name"] == "Idempotency-Key":
                        parameter["required"] = path != "/api/v1/knowledge/imports/feishu"
                        if path == "/api/v1/knowledge/imports/feishu":
                            parameter["description"] = (
                                "Required when dry_run=false; dry-run does not persist a receipt."
                            )
        return schema

    app.openapi = openapi
