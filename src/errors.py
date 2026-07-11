"""Shared error types."""


class AnotherBrainError(Exception):
    """Base class for all service errors."""


class ValidationError(AnotherBrainError):
    """A tool input or domain value failed validation."""


class ConfigError(AnotherBrainError):
    """Runtime configuration is missing or inconsistent."""
