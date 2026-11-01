class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None, retryable=False):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details, self.retryable = details or {}, retryable
