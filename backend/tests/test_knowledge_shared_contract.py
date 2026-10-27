"""Knowledge entry contract and explicit opt-in permission boundary."""

from sales_backend.domain.permission_defaults import ROLE_LABELS, default_permissions
from sales_backend.main import app


def test_knowledge_writes_publish_required_idempotency_key():
    operations = [
        (method, operation)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/v1/knowledge/") and not path.startswith("/api/v1/knowledge/distribution/")
        for method, operation in methods.items()
    ]
    # The provider binding and read-only status/count routes are part of the
    # current v1.2.2 contract. ``ask`` is a stateless read request, so it does
    # not create a durable mutation receipt and is intentionally excluded.
    # Source-file archive/read routes are part of the original-material browse
    # contract and add one read and one idempotent archive mutation.
    assert len(operations) == 29
    writes = [
        operation for method, operation in operations
        if method in {"post", "put"} and operation["operationId"] not in {
            "ask_api_v1_knowledge_ask_post", "import_feishu_api_v1_knowledge_imports_feishu_post",
        }
    ]
    assert len(writes) == 10
    for operation in writes:
        key = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
        assert key["required"] is True


def test_import_dry_run_key_is_optional_but_apply_contract_is_explicit():
    operation = app.openapi()["paths"]["/api/v1/knowledge/imports/feishu"]["post"]
    key = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
    assert key["required"] is False and "dry_run=false" in key["description"]


def test_no_builtin_role_implicitly_receives_knowledge_permissions():
    for role in ROLE_LABELS:
        assert not any(grant["permission"].startswith("knowledge.") for grant in default_permissions(role))


def test_all_knowledge_routes_have_explicit_shared_permission_gate():
    from sales_backend.api.knowledge import router
    from sales_backend.domain.route_permissions import ROUTES
    for route in router.routes:
        assert route.endpoint.__name__ in ROUTES["knowledge"], route.path
