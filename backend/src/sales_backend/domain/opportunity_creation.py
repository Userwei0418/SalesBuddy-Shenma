"""Creation-only failures; never expose arbitrary database exception text."""
CODES = {
    "OPPORTUNITY_CREATE_FORBIDDEN": "当前账号没有在该客户下创建商机的权限",
    "OPPORTUNITY_CUSTOMER_NOT_FOUND": "客户不存在或当前不可用于创建商机",
    "OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED": "该客户尚无有效认领，请先认领客户后再创建商机",
    "OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED": "该客户不在当前可创建商机的认领范围，请重新选择客户",
}


class OpportunityCreationError(Exception):
    def __init__(self, code):
        self.code = code if code in CODES else "OPPORTUNITY_CREATE_FORBIDDEN"
        self.status = 404 if self.code == "OPPORTUNITY_CUSTOMER_NOT_FOUND" else 403
        super().__init__(self.code)


def translate_creation_error(error):
    if getattr(error, "sqlstate", None) == "P0297":
        raise OpportunityCreationError(getattr(error, "message", None)) from error
    raise error
