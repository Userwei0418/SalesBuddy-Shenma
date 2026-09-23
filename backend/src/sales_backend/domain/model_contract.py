"""Safe diagnostic codes shared by strict business-model result contracts."""


class ModelContractError(ValueError):
    """Call sites supply a constant code, never model or user content."""

    def __init__(self, code):
        self.contract_code = code
        super().__init__(code)


def contract_failure_details(error):
    """Project only a known contract's fixed code through provider wrappers."""
    for _ in range(6):
        if isinstance(error, ModelContractError):
            return {"contract_code": error.contract_code}
        error = error.__cause__
        if error is None:
            break
    return {}
