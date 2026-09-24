"""Build business fixtures via operations creation, sales application and approval."""

from uuid import uuid4

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository


async def create_partner(connection, actor, name):
    from sales_backend.repositories.partners import PartnerRepository

    row = await connection.fetchrow("SELECT * FROM security.resolve_account_actor('demo-sales-workspace','OPS001',NULL)")
    operator = IdentityRepository._actor(row).context
    await set_request_context(connection, operator)
    partner = await PartnerRepository().save(connection, operator, {'name':name,'status':'active'})
    await set_request_context(connection, actor)
    return partner


async def create_owned_customer(connection, actor, *, data):
    row = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor('demo-sales-workspace','OPS001',NULL)"
    )
    operator = IdentityRepository._actor(row).context
    await set_request_context(connection, operator)
    customer = await CustomerMutationRepository().create(
        connection,
        operator,
        data={
            **data,
            "company_reference": "ISOLATED-" + uuid4().hex,
        },
    )
    await set_request_context(connection, actor)
    claim = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await set_request_context(connection, operator)
    await connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','隔离测试建档')", claim["request_id"]
    )
    await set_request_context(connection, actor)
    return customer


async def create_owned_opportunity(connection, actor, customer_id):
    """Minimal current customer-task association, owned by the fixture actor."""
    from sales_backend.repositories.authorization_checks import opportunity_creation_owner

    await set_request_context(connection, actor)
    owner = await opportunity_creation_owner(connection, actor, {})
    return await connection.fetchval(
        "INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,status,"
        "owner_user_ref_id,created_by_user_ref_id,owner_team_id) "
        "VALUES($1::uuid,$2::uuid,$4,1000,'open',$3::uuid,$3::uuid,$5::uuid) RETURNING id::text",
        actor.workspace_id, customer_id, actor.user_id, "隔离测试商机-" + uuid4().hex, owner["team_id"],
    )


async def authorize_opportunity_operations(connection, target):
    """This scenario exercises an operator explicitly granted delegated creation."""
    from sales_backend.contracts.authorization import AccountAuthorizationSave
    from sales_backend.repositories.authorization import AuthorizationRepository
    from tests.integration.test_operations_claims_sql import actor

    await actor(connection, "ADMIN001")
    repo = AuthorizationRepository()
    current = await repo.account(connection, target.user_id)
    await repo.save_account(connection, target.user_id, AccountAuthorizationSave(
        version_no=current["version_no"], reason="集成场景明确授予运营代建商机",
        overrides=[dict(permission=code, effect="allow", scope="workspace")
                   for code in ("opportunity.create", "opportunity.create_for_others")],
    ).model_dump(mode="json"))
    await set_request_context(connection, target)
