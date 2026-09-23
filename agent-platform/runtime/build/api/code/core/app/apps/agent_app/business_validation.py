"""Select sales rules from authenticated caller input, never from model output.

Input role/facts select validation only; they grant no platform or data permissions.
The original normalized value is deliberately NOT returned: some validators add
archival fields that would fail the sales system's second validation.
"""
import json
import time
from core.app.apps.agent_app.output_validation import OutputValidationError, _pairs, _constant, _float

RULE_VERSION='sales-adbb988-20260915'
ROUTING_VERSION='opportunity-routing-20260916'
ROLES={'sales','supervisor','manager','fde','fde_lead','operations','administrator'}

def prepare_business(policy, query, agent_id, snapshot_id):
    business=policy.get('business')
    if business is None:
        return None
    entry=business.get(snapshot_id)
    if entry is None or entry['agent_id']!=agent_id:
        raise OutputValidationError('business_snapshot_not_configured')
    try:
        request=json.loads(query,object_pairs_hook=_pairs,parse_constant=_constant,parse_float=_float)
    except (ValueError,TypeError,RecursionError):
        raise OutputValidationError('business_context_invalid_json') from None
    if not isinstance(request,dict) or not isinstance(request.get('facts'),dict):
        raise OutputValidationError('business_context_facts_required')
    mode=request.get('mode');cap=entry['capability'];facts=request['facts'];role=request.get('role')
    if not isinstance(role,str) or role not in ROLES:
        raise OutputValidationError('business_context_role_invalid')
    allowed={'chatbi','customer_chatbi'} if cap=='chatbi' else {cap}
    if not isinstance(mode,str) or mode not in allowed:
        raise OutputValidationError('business_context_mode_mismatch')
    contract=mode
    run_mode=mode
    if cap in {'visit_entry','visit_quality'}:
        stage='quality' if cap=='visit_quality' else 'structure'
        if facts.get('visit_stage')!=stage:
            raise OutputValidationError('business_context_visit_stage_mismatch')
        run_mode='visit_entry'
    elif cap in {'opportunity_draft','opportunity_advice'}:
        is_change = facts.get('contract_version') == 'opportunity_change_v1'
        if cap == 'opportunity_draft' and is_change:
            contract = 'opportunity_change'
        elif cap == 'opportunity_advice' and is_change:
            raise OutputValidationError('business_context_opportunity_route_mismatch')
    elif cap=='operating_report':
        if request.get('surface')=='fde_profile':
            contract='fde_coaching'
        elif role in {'sales','supervisor','manager','fde','fde_lead'}:
            contract='operating_report_'+role
        else:
            raise OutputValidationError('business_context_report_role_invalid')
    # Actor IDs and scope are unused by acceptance rules (only normalization).
    # Validation-only sentinels are never credentials and never returned.
    context={'contract':contract,'facts':facts,'binding_mode':entry.get('binding_mode','snapshot'),
             'actor':{'workspace_id':'validation-only','user_id':'validation-only','role':role,'data_scope':'self','team_ids':[]},
             'run':{'mode':run_mode,'surface':request.get('surface'),'text':request.get('user_text','')}}
    if cap=='competency_review':
        if not isinstance(facts.get('framework'),(dict,list)):
            raise OutputValidationError('business_context_framework_required')
        context['framework']=facts['framework']
    return context

def validate_business(answer, context, snapshot_id):
    from sales_contract_adapter import validate, explain_error
    start=time.perf_counter()
    try:
        validate(context['contract'],answer,context)
    except Exception as exc:
        # Never include rendered exception text/values; fixed sanitized diagnostics only.
        detail=explain_error(exc)
        error=OutputValidationError('business_contract_rejected')
        error.details={'contract':context['contract'],'rules_version':RULE_VERSION,'routing_version':ROUTING_VERSION,'errors':detail['errors']}
        raise error from None
    return {'business_validation':'passed','business_contract':context['contract'],
            'business_rules_version':RULE_VERSION,'business_routing_version':ROUTING_VERSION,'validated_snapshot_id':snapshot_id,
            'business_binding_mode':context.get('binding_mode','snapshot'),
            'business_validation_ms':round((time.perf_counter()-start)*1000,3)}
