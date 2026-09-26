class NovelFrameworkError(RuntimeError):
    """Base framework error."""


class SchemaError(NovelFrameworkError):
    """Persistent schema is missing, unsupported, or corrupt."""


class StateConflict(NovelFrameworkError):
    """A deterministic state transition cannot be applied safely."""


class ValidationBlocked(NovelFrameworkError):
    """A validation gate blocked the requested transition."""


class ProviderUnavailable(NovelFrameworkError):
    """An optional provider was requested but is unavailable."""
