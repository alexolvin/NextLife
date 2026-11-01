"""Exceptions hierarchy for NextLife."""


class NextLifeError(Exception):
    """Base class for all domain errors in NextLife."""


class ConfigError(NextLifeError):
    """Raised when configuration validation or loading fails."""


class SecretResolutionError(NextLifeError):
    """Raised when a SecretRef cannot be resolved."""


class InputError(NextLifeError):
    """Raised when CLI input or arguments are invalid."""


class ExternalSystemError(NextLifeError):
    """Raised when an external system or marketplace is unreachable."""


class OwnerDecisionRequired(NextLifeError):
    """Raised when an action requires manual decision from Owner."""
