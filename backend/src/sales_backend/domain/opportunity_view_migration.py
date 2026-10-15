"""Pure conservative v1 conversion. Unprovable equivalence never changes order."""

from sales_backend.contracts.opportunity_views import OpportunityViewConfig
from sales_backend.contracts.opportunity_workspace import OpportunityWorkspaceConfig


def convert_config(raw):
    if raw.get('schema_version') == 2:
        return OpportunityWorkspaceConfig.model_validate(raw).model_dump(mode='json', exclude_none=True), None
    OpportunityViewConfig.model_validate(raw)
    # Every v1 view orders by raw probability DESC NULLS LAST. Display stage
    # falls back to stage_code when probability is null, so replacing probability
    # with stage rank reorders even a single open stage. Schema 2 cannot express
    # that raw order. A currently non-null data sample cannot prove equivalence
    # for future records either. Preserve the stored v1 for explicit recreation.
    raise ValueError('旧方案按原始概率排序，无法等价转换；请重新确认筛选、列和排序后保存')
