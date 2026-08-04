"""Public error taxonomy used across the native runtime."""


class AnotherBrainError(Exception):
    """Base class for expected application failures."""


class ValidationError(AnotherBrainError):
    """Input or persisted data violates a hard contract."""


class ConfigError(AnotherBrainError):
    """Runtime configuration is invalid or incomplete."""


class ModelNotInstalledError(ConfigError):
    """The pinned local embedding model is not installed."""


class StorageBusyError(AnotherBrainError):
    """SQLite remained busy after bounded retries."""
