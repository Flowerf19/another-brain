"""Shared error types."""


class AnotherBrainError(Exception):
    """Base class for all service errors."""


class ValidationError(AnotherBrainError):
    """A tool input or domain value failed validation."""


class ConfigError(AnotherBrainError):
    """Runtime configuration is missing or inconsistent."""


class MigrationRequiredError(AnotherBrainError):
    """The existing Redis index does not match the configured contract
    (e.g. embedding dimension changed). The server must refuse to start
    until an explicit migration/reindex is performed (Step 04 section 5)."""

