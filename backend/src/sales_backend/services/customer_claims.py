from uuid import uuid4

from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.jobs import enqueue_battle_map_review
from sales_backend.services.capabilities import require_capability


async def claim_customer(connection, actor, customer_id):
    await require_capability(connection, actor, "customer.claim")
    result = await CustomerMemberRepository().claim(connection, customer_id)
    if result["added"]:
        await enqueue_battle_map_review(
            connection,
            actor,
            customer_id=customer_id,
            trigger_type="customer.claimed",
            trigger_id=str(uuid4()),
        )
    return result
