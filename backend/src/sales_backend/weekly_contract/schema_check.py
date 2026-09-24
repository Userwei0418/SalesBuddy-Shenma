"""Dependency-free validator for the JSON Schema keywords used by this handoff.

This is not a general JSON Schema implementation. Unknown assertion keywords
fail closed so future schema changes cannot silently bypass validation.
"""
import json
import math
from decimal import Decimal
import re
from datetime import date, datetime

KEYWORDS = {'$schema', 'title', 'description', 'type', 'const', 'enum', 'oneOf',
            'properties', 'required', 'additionalProperties', 'items',
            'uniqueItems', 'minItems', 'minLength', 'minimum', 'maximum', 'format'}


def schema_errors(value, schema, path='$'):
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise ValueError('Unsupported schema keyword: ' + ', '.join(sorted(unknown)))
    errors = []
    if 'oneOf' in schema:
        matches = sum(not schema_errors(value, branch, path) for branch in schema['oneOf'])
        if matches != 1:
            errors.append(path + ': expected exactly one matching schema')
    types = schema.get('type')
    types = [types] if isinstance(types, str) else types
    checks = {
        'null': value is None, 'object': isinstance(value, dict),
        'array': isinstance(value, list), 'string': isinstance(value, str),
        'boolean': type(value) is bool,
        'integer': type(value) is int,
        'number': type(value) in (int, float, Decimal) and math.isfinite(value),
    }
    if types and not any(checks.get(t, False) for t in types):
        return errors + [path + ': wrong type']
    if 'const' in schema and value != schema['const']:
        errors.append(path + ': wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(path + ': unknown enum value')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append(path + '.' + key + ': required')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            errors.extend(path + '.' + str(k) + ': unexpected field' for k in value if k not in properties)
        for key, sub in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], sub, path + '.' + key))
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            errors.append(path + ': too few items')
        if schema.get('uniqueItems'):
            items = [json.dumps(v, sort_keys=True, ensure_ascii=False) for v in value]
            if len(items) != len(set(items)):
                errors.append(path + ': duplicate item')
        if 'items' in schema:
            for i, item in enumerate(value):
                errors.extend(schema_errors(item, schema['items'], f'{path}[{i}]'))
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            errors.append(path + ': too short')
        fmt = schema.get('format')
        try:
            if fmt == 'date':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError()
                date.fromisoformat(value)
            elif fmt == 'date-time':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
                    raise ValueError()
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.utcoffset() is None:
                    raise ValueError()
            elif fmt is not None:
                raise ValueError('Unsupported format ' + fmt)
        except ValueError:
            errors.append(path + ': invalid ' + str(fmt))
    if type(value) in (int, float):
        if not math.isfinite(value):
            errors.append(path + ': non-finite number')
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(path + ': below minimum')
        if 'maximum' in schema and value > schema['maximum']:
            errors.append(path + ': above maximum')
    return errors
