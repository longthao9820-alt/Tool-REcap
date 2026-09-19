"""Typed domain failures. Every failure is explicit; nothing fails open."""


class RecapError(Exception):
    """Base class for all typed domain failures."""


class ValidationError(RecapError):
    """A value violated a domain invariant."""


class SourceMissingError(RecapError):
    """The referenced source file does not exist or is not a regular file."""


class SourceIdentityMismatchError(RecapError):
    """Source bytes changed relative to a previously recorded identity."""


class ProjectNotFoundError(RecapError):
    """The referenced project is not persisted, so no authoritative root exists."""


class CheckpointIntegrityError(RecapError):
    """A completed checkpoint references missing or unverifiable evidence."""


class ProbeUnavailableError(RecapError):
    """The media probe backend could not be executed."""


class ProbeFailedError(RecapError):
    """The media probe ran but produced unusable output."""


class UnsafeArtifactPathError(RecapError):
    """A generated artifact destination escapes the project root."""


class JobConflictError(RecapError):
    """A job identity is already owned by another active execution."""


class ProviderUnavailableError(RecapError):
    """A required provider capability is not available in this environment."""


class ProviderOutputInvalidError(RecapError):
    """A provider returned output that does not satisfy its DTO contract."""


class ProviderNotAuthorizedError(RecapError):
    """A provider is not permitted by the production composition root."""


class MediaProcessingError(RecapError):
    """A local media processing command failed or produced unusable output."""


class RevisionMismatchError(RecapError):
    """An entity reference crosses analysis/recap revision boundaries."""


class SchemaValidationError(RecapError):
    """A document violates its published JSON schema."""


class StageDependencyError(RecapError):
    """A stage cannot run because a predecessor output is missing or invalid."""


class BudgetInfeasibleError(RecapError):
    """A recap plan cannot satisfy MUST_HAVE coverage inside its hard bounds."""
