class UseCaseError(Exception):
    def __init__(
        self, code: str, message: str, *, audit_recorded: bool = False
    ) -> None:
        self.code = code
        self.audit_recorded = audit_recorded
        super().__init__(message)
