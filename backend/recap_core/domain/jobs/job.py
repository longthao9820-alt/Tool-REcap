"""Durable job identity and state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..errors import ValidationError
from ..identity import hash_text


class JobState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.CANCELLED})

_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.READY, JobState.CANCELLED}),
    JobState.READY: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}
    ),
    JobState.FAILED: frozenset({JobState.READY, JobState.CANCELLED}),
    JobState.INTERRUPTED: frozenset({JobState.READY, JobState.CANCELLED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


def can_transition(current: JobState, target: JobState) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def assert_transition(current: JobState, target: JobState) -> None:
    if not can_transition(current, target):
        raise ValidationError(f"illegal job transition {current.value} -> {target.value}")


def job_input_hash(stage: str, scope_type: str, scope_id: str, *evidence: str) -> str:
    """Idempotency key over stage, scope and causally relevant input identity."""
    return hash_text(stage, scope_type, scope_id, *evidence)


@dataclass(frozen=True)
class Job:
    id: str
    project_id: str
    stage: str
    scope_type: str
    scope_id: str
    input_hash: str
    state: JobState = JobState.PENDING
    attempts: int = 0
    max_attempts: int = 3
    error_json: str | None = None

    def __post_init__(self) -> None:
        if not self.stage.strip():
            raise ValidationError("job stage must not be empty")
        if self.attempts < 0:
            raise ValidationError("job attempts must not be negative")
        if self.max_attempts < 1:
            raise ValidationError("job max_attempts must be >= 1")

    def with_state(self, target: JobState, error_json: str | None = None) -> "Job":
        assert_transition(self.state, target)
        attempts = self.attempts + 1 if target is JobState.RUNNING else self.attempts
        return Job(
            id=self.id,
            project_id=self.project_id,
            stage=self.stage,
            scope_type=self.scope_type,
            scope_id=self.scope_id,
            input_hash=self.input_hash,
            state=target,
            attempts=attempts,
            max_attempts=self.max_attempts,
            error_json=error_json,
        )
