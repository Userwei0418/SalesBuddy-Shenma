class ModelContractError(ValueError):
    """Call sites supply a constant code, never model or user content."""

    def __init__(self, code):
        self.contract_code = code
        super().__init__(code)
