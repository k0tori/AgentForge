"""Sandbox failures. Callers must not run untrusted code on the host after one of these."""


class SandboxUnavailableError(RuntimeError):
    """The configured sandbox cannot run. This is a hard failure, not a fallback."""

    def __init__(self, message: str, *, code: str = "sandbox_unavailable") -> None:
        super().__init__(message)
        self.code = code
