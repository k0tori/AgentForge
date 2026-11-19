"""Sandbox failures. Callers must not run untrusted code on the host after one of these."""


class SandboxUnavailableError(RuntimeError):
    """The configured sandbox cannot run. This is a hard failure, not a fallback."""
