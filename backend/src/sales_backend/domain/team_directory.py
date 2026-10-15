"""Team selector actions; a proposed assignment does not grant a business write."""
from typing import Literal

from sales_backend.domain.business_read import BUSINESS_READ_PERMISSIONS, ReadPermission

TeamPurpose = Literal['browse', 'dashboard', 'profile', 'fde', 'assignment']
AssignmentPermission = Literal['customer.create', 'customer.create_request']
TeamDirectoryPermission = ReadPermission | AssignmentPermission
PURPOSE_PERMISSIONS = {
    'browse': 'directory.read', 'dashboard': 'dashboard.read', 'profile': 'profile.sales_read',
    'fde': 'profile.fde_read', 'assignment': 'customer.create',
}
ASSIGNMENT_PERMISSIONS = frozenset({'customer.create', 'customer.create_request'})


def team_directory_permission(purpose='browse', permission=None):
    if purpose not in PURPOSE_PERMISSIONS:
        raise ValueError('未知团队目录用途')
    selected = permission if permission is not None else PURPOSE_PERMISSIONS[purpose]
    if selected not in BUSINESS_READ_PERMISSIONS and not (
        purpose == 'assignment' and selected in ASSIGNMENT_PERMISSIONS
    ) and not (
        purpose == 'dashboard' and selected == 'dashboard.ranking'
    ):
        raise ValueError('此团队目录用途不支持指定权限')
    return selected
