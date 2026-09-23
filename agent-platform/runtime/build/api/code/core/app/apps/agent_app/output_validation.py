"""Deterministic output validation; no repair, model retry, or remote schema fetch."""
import hashlib
import json
import math
from pathlib import Path

class OutputValidationError(ValueError):
    pass

def policy_for(app_id, snapshot_id):
    config = json.loads(Path(__file__).with_name('output_contracts.json').read_text())
    entry = config.get(app_id)
    if not entry:
        return None
    # Business schemas are bound to an immutable published snapshot.
    schema = entry.get('snapshots', {}).get(snapshot_id)
    business = entry.get('business')
    default = entry.get('business_default')
    if default is not None and isinstance(snapshot_id, str) and snapshot_id:
        # The native runner has resolved this app/Agent/config under existing
        # tenant authorization. Inherit the Agent's contract for new versions;
        # an explicit snapshot rule always takes priority.
        business = dict(business or {})
        if snapshot_id not in business:
            business[snapshot_id] = dict(default, binding_mode='agent_inherited')
    return {'schema': schema, 'business': business}

def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise OutputValidationError('duplicate_json_key')
        obj[key] = value
    return obj

def _constant(value):
    raise OutputValidationError('non_finite_number')

def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise OutputValidationError('non_finite_number')
    return number

def _local_schema(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ('$ref', '$dynamicRef') and (not isinstance(child, str) or not child.startswith('#')):
                raise OutputValidationError('external_schema_reference_forbidden')
            _local_schema(child)
    elif isinstance(value, list):
        for child in value:
            _local_schema(child)

def normalize_and_validate(answer, schema=None):
    text = answer.strip()
    lines = text.splitlines()
    stripped = False
    if len(lines) >= 3 and lines[0].strip().lower() in ('```', '```json', '~~~', '~~~json'):
        marker = lines[0].strip()[:3]
        if lines[-1].strip() != marker:
            raise OutputValidationError('invalid_json_envelope')
        text = '\n'.join(lines[1:-1]).strip()
        stripped = True
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise OutputValidationError('invalid_json') from exc
    if not isinstance(value, dict):
        raise OutputValidationError('json_object_required')
    if schema is not None:
        from jsonschema import Draft202012Validator
        _local_schema(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise OutputValidationError('invalid_business_schema') from exc
        error = next(Draft202012Validator(schema).iter_errors(value), None)
        if error is not None:
            # Do not echo model content or business data in API errors.
            raise OutputValidationError('business_schema_' + str(error.validator))
    return text, {
        'json_valid': True,
        'fence_removed': stripped,
        'business_validation': 'passed' if schema is not None else 'not_configured',
        'raw_answer_sha256': hashlib.sha256(answer.encode()).hexdigest(),
    }
