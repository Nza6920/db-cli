from __future__ import annotations


class RunnerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int,
        **details: object,
    ):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details
