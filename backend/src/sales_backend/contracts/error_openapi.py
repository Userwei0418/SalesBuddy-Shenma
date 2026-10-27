"""Add the public failure envelope without removing endpoint error contracts."""

from sales_backend.domain.opportunity_creation import CODES as OPPORTUNITY_CREATION_ERRORS
from sales_backend.domain.collection_actual_errors import COLLECTION_ACTUAL_ERRORS
from sales_backend.domain.recognized_actual_errors import RECOGNIZED_ACTUAL_ERRORS


def install_error_openapi(app):
    original = app.openapi

    def openapi():
        schema = original()
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {})["PublicApiError"] = {
            "type": "object",
            "required": ["code", "message", "detail", "request_id", "retryable", "recovery_action"],
            "properties": {
                "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,99}$",
                         "description": "Stable business code; HTTP status remains independent. Unknown codes use a safe status-based client message."},
                "message": {"type": "string", "description": "Reviewed Chinese user message; never raw exception, SQL, validation input or field identifier."},
                "detail": {"description": "Legacy string/object/validation-array contract. Machine field locations are not display labels.",
                           "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array", "items": {"type": "object"}}]},
                "request_id": {"type": "string", "format": "uuid"},
                "retryable": {"type": "boolean", "description": "True only when retrying a read is permitted. Never authorizes replaying a mutation."},
                "recovery_action": {"type": "string", "enum": ["retry_read", "check_status", "correct_request", "login", "contact_admin"]},
            },
            "additionalProperties": True,
        }
        components.setdefault("responses", {})["PublicApiFailure"] = {
            "description": "Public API failure. Preserve the original HTTP status and business code; show safe message only. Mutations with unknown outcome require receipt/status lookup, never blind retry.",
            "headers": {"X-Request-Id": {"schema": {"type": "string", "format": "uuid"}},
                        "Cache-Control": {"schema": {"type": "string", "const": "no-store"}}},
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PublicApiError"}}},
        }
        for path, methods in schema["paths"].items():
            if not path.startswith("/api/") or path.startswith("/api/v1/health/"):
                continue
            for method, operation in methods.items():
                if method not in {"get", "head", "post", "put", "patch", "delete"}:
                    continue
                # Explicit domain-specific responses retain their schemas and
                # documentation. The default supplies the shared envelope for
                # all statuses, including framework 404/405 and uncaught 500.
                operation.setdefault("responses", {}).setdefault(
                    "default", {"$ref": "#/components/responses/PublicApiFailure"})
                operation["x-error-envelope"] = {"$ref": "#/components/schemas/PublicApiError"}
                if method == "post" and path in {
                    "/api/v1/customers/{customer_id}/opportunities", "/api/v1/console/opportunities", "/api/v1/visits",
                }:
                    operation["x-opportunity-creation-error-codes"] = {
                        code: {"status": 404 if code == "OPPORTUNITY_CUSTOMER_NOT_FOUND" else 403, "message": message}
                        for code, message in OPPORTUNITY_CREATION_ERRORS.items()
                    }
                    operation["description"] = (operation.get("description", "") +
                        " 仅新建商机须通过当前有效认领与创建者有效组织任职范围；保存原子重检。"
                        "客户认领人不等于指定商机负责人，负责人/团队/create_for_others仍按原授权检查。"
                        "已有商机编辑、查看、跟进不受此新增门槛影响；并发冲突仍返回409。")
                if method == "post" and path in {
                    "/api/v1/review/targets", "/api/v1/console/targets", "/api/v1/console/targets/batch",
                }:
                    operation["x-target-error-codes"] = {
                        "TARGET_INPUT_INVALID": 422, "TARGET_METRIC_UNAVAILABLE": 422,
                        "TARGET_VALUE_INVALID": 422, "VERSION_CONFLICT": 409,
                        "TARGET_PENDING_CONFLICT": 409,
                    }
                    operation["description"] = (operation.get("description", "") +
                        " 目标金额接口使用元（最多两位小数），后台以万元精确换算。指标或金额格式错误返回422；"
                        "版本过期与待审批冲突保持409，不自动重试或覆盖。")
                if method == "post" and path in {
                    "/api/v1/customers/{customer_id}/opportunities",
                    "/api/v1/console/opportunities",
                }:
                    operation["x-collection-actual-error-codes"] = {
                        code: {"status": value[0], "message": value[1]}
                        for code, value in COLLECTION_ACTUAL_ERRORS.items()
                    }
                    operation["x-recognized-actual-error-codes"] = {
                        code: {"status": value[0], "message": value[1]}
                        for code, value in RECOGNIZED_ACTUAL_ERRORS.items()
                    }
                    for status_code in (403, 409, 422):
                        descriptions = [f"{code}: {message}" for code, (status, message)
                                        in (COLLECTION_ACTUAL_ERRORS | RECOGNIZED_ACTUAL_ERRORS).items() if status == status_code]
                        description = "季度实绩保存拒绝：" + "；".join(descriptions) + "。"
                        if status_code == 422:
                            description += (
                                "字段格式校验仍返回 VALIDATION_ERROR 与安全 loc/type 数组；"
                                "客户端将字段位置映射为中文提示，不展示 input、原始 msg 或 SQL。"
                            )
                        operation["responses"][str(status_code)] = {
                            "description": description,
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/PublicApiError"},
                            }},
                        }
                if method == "post" and path in {
                    "/api/v1/auth/password/login", "/api/v1/console/auth/login",
                }:
                    operation["responses"]["401"] = {
                        "description": (
                            "INVALID_CREDENTIALS: 账号或密码错误。账号不存在、密码错误或登录标识不唯一"
                            "使用相同响应，不泄露账号存在性。workspace 可省略，由唯一账号或已绑定手机号"
                            "识别公司；不按密码跨公司尝试。此错误 recovery_action=correct_request，"
                            "不代表已有业务会话失效。"
                        ),
                        "content": {"application/json": {
                            "schema": {"$ref": "#/components/schemas/PublicApiError"},
                        }},
                    }
        return schema

    app.openapi = openapi
