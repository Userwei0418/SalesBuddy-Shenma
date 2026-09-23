"""Expose only outputs of the built-in knowledge search, never other tool data."""
import json


def knowledge_outputs(thoughts):
    result = []
    for thought in thoughts:
        names = thought.tool
        output = thought.observation
        try:
            names = json.loads(names)
        except (ValueError, TypeError):
            pass
        if isinstance(names, list):
            try:
                outputs = json.loads(output)
            except (ValueError, TypeError):
                continue
            if not isinstance(outputs, list):
                continue
            result.extend(value for name, value in zip(names, outputs) if name == 'knowledge_base_search' and isinstance(value, str))
        elif names == 'knowledge_base_search' and isinstance(output, str):
            result.append(output)
    return list(dict.fromkeys(result))
