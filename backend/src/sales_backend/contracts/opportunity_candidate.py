"""Model candidates are optional, explicitly associated and never business commands."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

PROMPT_VERSION = 'opportunity-candidate-v2-20260910'


class OpportunityCandidate(BaseModel):
    action: Literal['none', 'create', 'update']
    opportunity_id: str = ''
    name: str = ''
    probability: Literal[10, 30, 50, 70, 90, 100] | None = None
    status: Literal['open', 'won', 'lost'] | None = None
    expected_close_date: date | None = None
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    partner_name: str = ''
    product_line: str = ''
    rationale: str = ''
    title: str = ''
    summary: str = ''
    missing_fields: list[str] = Field(default_factory=list)


def validate_candidate(result, facts):
    candidate = OpportunityCandidate.model_validate(result)
    if candidate.action == 'update':
        visible_ids = {item['id'] for item in facts.get('current_opportunities', [])}
        if candidate.opportunity_id not in visible_ids:
            raise ValueError('商机候选引用了当前权限事实之外的商机')
    elif candidate.opportunity_id:
        raise ValueError('未选择更新时不能携带已有商机 ID')
    if candidate.action == 'none':
        candidate = OpportunityCandidate(action='none', rationale=candidate.rationale,
                                         summary=candidate.summary, title='本次不关联商机')
    return {**candidate.model_dump(mode='json'), 'prompt_version': PROMPT_VERSION}
