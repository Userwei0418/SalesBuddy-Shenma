"""Compose the shared router with a report job's transport and lifetime guards."""

import asyncio
import logging
from copy import deepcopy
from time import monotonic

from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    AgentUnavailable,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    Validate,
    WriteState,
)

from .providers import AssertActive, GenerationEnvelope, GenerationProviders

logger = logging.getLogger(__name__)


class GenerationStateUnavailable(RuntimeError):
    """A job/permission check failed; this is never an inference-route failure."""


async def route_generation(
    envelope: GenerationEnvelope,
    *,
    providers: GenerationProviders,
    assert_active: AssertActive,
    routing_policy: RoutingPolicy,
    validate: Validate,
    observer=None,
    deadline: float | None = None,
):
    """Return only an accepted, still-current candidate; never persist it.

    Backend must also compare job cancellation and document versions in the
    final persistence transaction. These checks do not replace that write fence.
    """
    frozen_envelope = deepcopy(envelope)
    platform_started = False
    seal_attempted = False
    authority_unresolved = False
    deadline = deadline if deadline is not None else monotonic() + routing_policy.total_seconds

    def remaining():
        return max(0, deadline - monotonic())

    async def active():
        nonlocal authority_unresolved
        authority_unresolved = True
        try:
            await assert_active()
            if remaining() <= 0:
                raise GenerationStateUnavailable("work-report authority budget exhausted")
            authority_unresolved = False
        except (TimeoutError, ProviderUnavailable) as exc:
            # AgentRouter treats these as fallback candidates when raised by a
            # provider. A failed authority check must not select another model.
            raise GenerationStateUnavailable("work-report state could not be verified") from exc

    async def platform():
        nonlocal platform_started
        outgoing = deepcopy(frozen_envelope)
        await active()
        if providers.platform is None:
            raise ProviderUnavailable("work-report platform is not configured")
        platform_started = True
        result = await providers.platform(outgoing)
        await active()
        return result

    async def direct():
        outgoing = deepcopy(frozen_envelope)
        await active()
        result = await providers.direct(outgoing)
        await active()
        return result

    async def seal():
        nonlocal seal_attempted
        seal_attempted = True
        state = await providers.seal_platform() if platform_started else WriteState.NONE
        if authority_unresolved:
            raise GenerationStateUnavailable("work-report authority remains unverified")
        return state

    try:
        await active()
        budget = remaining()
        if budget <= 0:
            raise GenerationStateUnavailable("work-report authority budget exhausted")
        current_policy = RoutingPolicy(
            platform_enabled=routing_policy.platform_enabled, total_seconds=budget,
            platform_seconds=min(routing_policy.platform_seconds, budget / 2),
            receipt_seconds=min(routing_policy.receipt_seconds, budget / 6),
        )
        result = await AgentRouter().evaluate(
            policy=current_policy,
            platform=platform,
            direct=direct,
            validate=validate,
            write_state=seal,
            observe=observer,
            fallback_provider="direct",
        )
        if platform_started and not seal_attempted:
            try:
                async with asyncio.timeout(min(routing_policy.receipt_seconds, remaining())):
                    state = await seal()
            except Exception as exc:
                raise ReconciliationRequired("work-report operation seal is unavailable") from exc
            if state is not WriteState.NONE:
                raise ReconciliationRequired("work-report operation may have business writes")
        await active()
        return result
    except (AgentUnavailable, ReconciliationRequired):
        # Router timeouts can cancel a provider while it is awaiting authority.
        # Such a timeout must not authorize another route or a basic summary.
        if authority_unresolved:
            raise GenerationStateUnavailable("work-report authority remains unverified") from None
        raise
    finally:
        if platform_started and not seal_attempted:
            # Cancellation/permission failures retain their original exception.
            # This bounded cleanup is not proof that a remote request stopped.
            try:
                async with asyncio.timeout(min(routing_policy.receipt_seconds, remaining())):
                    await seal()
            except (Exception, asyncio.CancelledError):
                logger.warning("work-report transport cleanup remains unconfirmed")
