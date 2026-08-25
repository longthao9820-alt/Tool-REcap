"""Typed domain failures. Every failure is explicit; nothing fails open."""


class RecapError(Exception):
    """Base class for all typed domain failures."""


class ValidationError(RecapError):
    """A value violated a domain invariant."""


class SourceMissingError(RecapError):
    """The referenced source file does not exist or is not a regular file."""


class SourceIdentityMismatchError(RecapError):
    """Source bytes changed relative to a previously recorded identity."""


class ProbeUnavailableError(RecapError):
    """The media probe backend could not be executed."""


class ProbeFailedError(RecapError):
    """The media probe ran but produced unusable output."""


class UnsafeArtifactPathError(RecapError):
    """A generated artifact destination escapes the project root."""


class JobConflictError(RecapError):
    """A job identity is already owned by another active execution."""
