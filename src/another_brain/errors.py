"""Shared error types for the clean runtime."""


class BrainError(Exception):
    """Base class for all another-brain errors."""


class ConfigError(BrainError):
    """Invalid configuration; raised at startup, never later."""


class ValidationError(BrainError):
    """Invalid user/tool input; carries an actionable message."""


class ModelInstallError(BrainError):
    """Model download/install failed; the profile is left uninstalled."""


class ModelDownloadError(ModelInstallError):
    """Network-level failure (unreachable, interrupted, HTTP error)."""


class ModelHashMismatchError(ModelInstallError):
    """A downloaded file does not match its pinned SHA-256."""

    def __init__(self, name: str, expected: str, actual: str) -> None:
        super().__init__(f"hash mismatch for {name}: expected {expected}, got {actual}")
        self.name = name
        self.expected = expected
        self.actual = actual
