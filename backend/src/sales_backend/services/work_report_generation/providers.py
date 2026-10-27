"""Authorized transport callbacks supplied by the backend worker.

This module does not select credentials, authorize a tenant, access the database,
or instantiate a client. Both transports consume the same generation envelope.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sales_backend.services.agent_platform.routing import WriteState

GenerationEnvelope = dict[str, Any]
ProviderInvoke = Callable[[GenerationEnvelope], Awaitable[dict[str, Any]]]
PlatformSeal = Callable[[], Awaitable[WriteState]]
AssertActive = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class GenerationProviders:
    """No IO at construction; absence of a platform means direct-only execution.

    The worker maps transport failures to ProviderUnavailable. Authorization,
    cancellation, lost leases, and input failures must remain distinct failures.
    An installed platform callback always needs a trusted lifetime seal, even
    when no business-tool authority was issued for this operation.
    Each platform(envelope) invocation must allocate a fresh operation and bind
    seal_platform to that invocation's durable receipt. Batches run sequentially;
    never reuse a sealed operation across them or share this closure across jobs.
    """

    direct: ProviderInvoke = field(repr=False)
    platform: ProviderInvoke | None = field(default=None, repr=False)
    seal_platform: PlatformSeal | None = field(default=None, repr=False)

    def __post_init__(self):
        if not callable(self.direct):
            raise TypeError("work-report direct provider must be callable")
        if (self.platform is None) != (self.seal_platform is None):
            raise ValueError("work-report platform and lifetime seal must be provided together")
        if self.platform is not None and (
            not callable(self.platform) or not callable(self.seal_platform)
        ):
            raise TypeError("work-report platform and lifetime seal must be callable")
