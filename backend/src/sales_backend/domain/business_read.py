"""Business read actions that support dynamic effective-department templates.

Only configuration accepts ``departments``. Effective grants resolve it to
``teams`` and current team IDs; identity scope and every write action stay separate.
"""

from typing import Literal

BUSINESS_READ_PERMISSIONS = frozenset(
    (
        "directory.read",
        "overview.read",
        "battle_map.read",
        "customer.read",
        "opportunity.read",
        "visit.read",
        "task.read",
        "risk.read",
        "dashboard.read",
        "profile.sales_read",
        "profile.fde_read",
        "profile.fde_activity",
        "actual.read",
        "demo_scene.read",
        "target.read",
        "target.history",
    )
)
ReadPermission = Literal[
    "directory.read",
    "overview.read",
    "battle_map.read",
    "customer.read",
    "opportunity.read",
    "visit.read",
    "task.read",
    "risk.read",
    "dashboard.read",
    "profile.sales_read",
    "profile.fde_read",
    "profile.fde_activity",
    "actual.read",
    "demo_scene.read",
    "target.read",
    "target.history",
]
