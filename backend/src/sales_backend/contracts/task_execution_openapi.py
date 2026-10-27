"""Publish the action-specific runtime validators on the shared execution route."""

from pydantic.json_schema import models_json_schema

from sales_backend.contracts.task_execution import COMMAND_MODELS
from sales_backend.contracts.task_execution_v3 import COMMAND_MODELS_V3


def install_execution_openapi(app):
    original = app.openapi

    def openapi():
        schema = original()
        if schema.get("x-task-execution-protocol") == 3:
            return schema
        refs, definitions = models_json_schema(
            [(model, "validation") for model in dict.fromkeys(COMMAND_MODELS.values())],
            ref_template="#/components/schemas/{model}",
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions.get("$defs", {}))
        operation = schema["paths"]["/api/v1/tasks/{task_id}/execution/{action}"]["post"]
        for parameter in operation["parameters"]:
            if parameter["name"] == "action":
                parameter["schema"] = {"type": "string", "enum": list(COMMAND_MODELS)}
            if parameter["name"] == "Idempotency-Key":
                parameter["required"] = True
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": list(refs.values()),
                        "description": ("Use the schema mapped to the action. "
                                        "V1 keeps three positive integer versions. "
                                        "V2 additionally requires checklist_revision. "
                                        "Checklist edits are direct owner commands; "
                                        "overall requirements never contain a V2 checklist."),
                        "x-action-schemas": {
                            action: refs[(model, "validation")] for action, model in COMMAND_MODELS.items()
                        },
                    }
                }
            },
        }
        refs_v3, definitions_v3 = models_json_schema(
            [(model, "validation") for model in dict.fromkeys(COMMAND_MODELS_V3.values())],
            ref_template="#/components/schemas/{model}",
        )
        schema["components"]["schemas"].update(definitions_v3.get("$defs", {}))
        operation_v3 = schema["paths"]["/api/v1/tasks/{task_id}/execution-v3/{action}"]["post"]
        for parameter in operation_v3["parameters"]:
            if parameter["name"] == "action":
                parameter["schema"] = {"type": "string", "enum": list(COMMAND_MODELS_V3)}
            if parameter["name"] == "Idempotency-Key":
                parameter["required"] = True
        operation_v3["requestBody"] = {"required": True, "content": {"application/json": {"schema": {
            "anyOf": list(refs_v3.values()),
            "description": "Protocol 3: three optimistic versions, no checklist or contribution. Review binds one submission.",
            "x-action-schemas": {action: refs_v3[(model, "validation")] for action, model in COMMAND_MODELS_V3.items()},
        }}}}
        schema["x-task-execution-protocol"] = 3
        return schema

    app.openapi = openapi
