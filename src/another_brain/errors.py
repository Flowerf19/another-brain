"""Shared error types for the clean runtime."""


class BrainError(Exception):
    """Base class for all another-brain errors."""


class ConfigError(BrainError):
    """Invalid configuration; raised at startup, never later."""


class ValidationError(BrainError):
    """Invalid user/tool input; carries an actionable message."""
