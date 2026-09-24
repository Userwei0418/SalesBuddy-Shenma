"""Coverage comes from the real route declarations, not a duplicated URL list."""
import ast
from pathlib import Path

from sales_backend.domain.permission_catalog import CATALOG
from sales_backend.domain.route_permissions import ROUTES, EXPORTS, AGENT_PERMISSIONS, TASK_EVENTS, route_permission


def test_every_declared_http_handler_is_classified():
    directory = Path(__file__).parents[1] / 'src/sales_backend/api'
    for path in directory.glob('*.py'):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in {'get','post','put','patch','delete'}:
                    assert node.name in ROUTES.get(path.stem, {}), (path.name,node.name)


def test_action_gate_only_uses_known_permissions_and_unknown_handler_is_closed():
    import pytest
    for handlers in ROUTES.values():
        for code in handlers.values():
            assert code=='*' or code.startswith('$') or code in CATALOG
    for code in [*EXPORTS.values(), *AGENT_PERMISSIONS.values(), *TASK_EVENTS.values()]:
        assert code in CATALOG
    with pytest.raises(PermissionError):
        route_permission('new_module','new_unreviewed_handler')
    assert route_permission('operations_customers','customers',exporting=True)=='customer.export'
