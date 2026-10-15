from decimal import Decimal

import pytest

from sales_backend.domain.route_permissions import route_permission
from sales_backend.services.customer_map import has_map_scores


@pytest.mark.parametrize("value,expected", [
    (0, True), (100, True), (Decimal("70.5"), True), (None, False),
    (True, False), ("", False), (float("nan"), False), (float("inf"), False), (-1, False), (101, False),
])
def test_only_complete_valid_coordinates_count_as_map_ready(value, expected):
    assert has_map_scores({"potential_score": value, "relationship_score": 50}) is expected
    assert has_map_scores({"potential_score": 50, "relationship_score": value}) is expected


def test_refresh_requires_existing_battle_map_permission():
    assert route_permission("customer_assets", "refresh_map") == "battle_map.read"


@pytest.mark.parametrize("path,method", [
    ("/api/v1/customer-assets/map", "get"),
    ("/api/v1/customer-assets/map/refresh", "post"),
])
def test_map_scope_contract_only_advertises_supported_subject_scopes(path, method):
    from sales_backend.main import app

    operation = app.openapi()["paths"][path][method]
    parameter = next(item for item in operation["parameters"] if item["name"] == "scope")
    choices = next(item["enum"] for item in parameter["schema"]["anyOf"] if "enum" in item)
    assert choices == ["self", "person", "team"]
