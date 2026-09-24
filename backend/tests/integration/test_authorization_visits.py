"""Explicit visit scopes and mid-flight revocation without statistical role changes."""
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.visits import VisitRepository
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.services.agent_access import require_agent_access
from sales_backend.services.visit_access import require_visit_recording_scope
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import fields_for
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_explicit_fde_self_visit_permission_allows_customer_only_without_changing_identity(connection):
    _, opportunity, people, _ = await fde_fixture(connection)
    first = await actor(connection, people['first']['code'])
    customer_id = opportunity['customer_id']
    with pytest.raises(PermissionError):
        await require_visit_recording_scope(connection, first, customer_id, None)
    admin = await actor(connection, 'ADMIN001')
    await grant(connection, admin, first.user_id, [dict(permission='visit.create', effect='allow', scope='self')])
    await set_request_context(connection, first)
    await require_visit_recording_scope(connection, first, customer_id, None)
    visit = await VisitRepository().create(connection, first, customer_id=customer_id,
        fields={**fields_for(opportunity), 'opportunity_id': None})
    assert await connection.fetchval('SELECT recording_role_code_snapshot FROM activity.visit WHERE id=$1::uuid', visit['id']) == 'fde'
    await grant(connection, admin, first.user_id, [], version=1)
    await set_request_context(connection, first)
    with pytest.raises(PermissionError):
        await require_visit_recording_scope(connection, first, customer_id, None)


async def test_sales_analysis_snapshot_invalidated_by_any_effective_permission_change(connection):
    admin = await actor(connection, 'ADMIN001')
    sales = await actor(connection, 'XS001')
    snapshot = await CapabilityRepository().analysis_identity(connection, sales)
    await require_agent_access(connection, sales, 'chatbi', permission_version=snapshot['permission_version'])
    await grant(connection, admin, sales.user_id, [dict(permission='agent.chatbi', effect='deny')])
    await set_request_context(connection, sales)
    with pytest.raises(PermissionError):
        await require_agent_access(connection, sales, 'chatbi', permission_version=snapshot['permission_version'])
    # Even restoring the action does not make a different scoped snapshot current.
    await grant(connection, admin, sales.user_id,
        [dict(permission='agent.chatbi', effect='allow', scope='teams', team_ids=list(sales.team_ids))], version=1)
    await set_request_context(connection, sales)
    with pytest.raises(PermissionError, match='权限已变化'):
        await require_agent_access(connection, sales, 'chatbi', permission_version=snapshot['permission_version'])
