"""A projection uses exactly its own scope and never opens unrelated detail APIs."""
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login, opportunity_payload
from sales_backend.contracts.models import OpportunityCreate
from sales_backend.services.opportunities import save_opportunity

pytestmark = pytest.mark.asyncio


async def test_dashboard_grant_has_its_own_scope_without_opportunity_detail_access(connection):
    customer = await create_customer(connection)
    owner = await actor(connection, 'XS001')
    opportunity = await save_opportunity(connection, owner, customer_id=customer['id'],
        data=OpportunityCreate.model_validate(opportunity_payload()).model_dump())
    user, _, admin = await account_fixture(connection, ['fde'])
    await grant(connection, admin, user['id'], [dict(permission='dashboard.read', effect='allow',
        scope='teams', team_ids=list(owner.team_ids))])
    async with await client_for(connection) as client:
        await native_login(client, user)
        dashboard = await client.get('/api/v1/dashboard')
        assert dashboard.status_code == 200, dashboard.text
        assert opportunity['id'] in {item['id'] for item in dashboard.json()['opportunities']}
        detail = await client.get(f"/api/v1/opportunities/{opportunity['id']}/detail")
        assert detail.status_code == 404, detail.text
        # A request field cannot substitute another feature or expand this scope.
        malicious = await client.get('/api/v1/dashboard', params={'team_groups': f'team:{uuid4()}'},
            headers={'X-Permission': 'opportunity.read', 'X-Data-Scope': 'workspace'})
        assert malicious.status_code in {403, 422}, malicious.text
        await grant(connection, admin, user['id'], [], version=1)
        assert (await client.get('/api/v1/dashboard')).status_code == 403
    # Request context has been reset; a subsequent ordinary transaction cannot retain it.
    await set_request_context(connection, owner)
    assert await connection.fetchval("SELECT current_setting('app.authorized_feature',true)") == ''
