"""Finite integer-millisecond time ranges shared by every analysis model."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError


def validate_ms(value: object, field_name: str) -> int:
    """Return a finite non-negative integer millisecond value or fail closed.

    Floats, bools, NaN/inf and numeric strings are rejected: milliseconds are the
    only authoritative unit in the pipeline, so no coercion happens here.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_name} must be an integer millisecond value, got {value!r}")
    if value < 0:
        raise ValidationError(f"{field_name} must not be negative, got {value!r}")
    return value


@dataclass(frozen=True, order=True)
class TimeRange:
    """Half-open `[start_ms, end_ms)` interval with a strictly positive length."""

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        validate_ms(self.start_ms, "start_ms")
        validate_ms(self.end_ms, "end_ms")
        if self.end_ms <= self.start_ms:
            raise ValidationError(
                f"end_ms must be greater than start_ms, got {self.start_ms}..{self.end_ms}"
            )

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def contains(self, other: "TimeRange") -> bool:
        return self.start_ms <= other.start_ms and other.end_ms <= self.end_ms

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start_ms < other.end_ms and other.start_ms < self.end_ms

    def assert_within_duration(self, duration_ms: int, label: str) -> None:
        """Fail closed when the range leaves the authoritative episode duration."""
        if self.end_ms > duration_ms:
            raise ValidationError(
                "{0} range {1}..{2} exceeds episode duration {3}".format(
                    label, self.start_ms, self.end_ms, duration_ms
                )
            )

    def assert_within(self, outer: "TimeRange", label: str) -> None:
        if not outer.contains(self):
            raise ValidationError(
                "{0} range {1}..{2} is not inside {3}..{4}".format(
                    label, self.start_ms, self.end_ms, outer.start_ms, outer.end_ms
                )
            )

    def to_dict(self) -> dict[str, int]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: object) -> "TimeRange":
        if not isinstance(data, dict):
            raise ValidationError(f"time range must be an object, got {data!r}")
        unknown = set(data) - {"start_ms", "end_ms"}
        if unknown:
            raise ValidationError(f"unknown time range fields: {sorted(unknown)}")
        try:
            return cls(start_ms=data["start_ms"], end_ms=data["end_ms"])
        except KeyError as exc:
            raise ValidationError(f"time range is missing {exc}") from exc


def validate_confidence(value: object, field_name: str = "confidence") -> float:
    """Return a finite confidence in `[0, 1]` or fail closed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be a number, got {value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValidationError(f"{field_name} must be finite, got {value!r}")
    if not 0.0 <= number <= 1.0:
        raise ValidationError(f"{field_name} must be within [0, 1], got {value!r}")
    return number


def validate_non_empty_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be non-empty text, got {value!r}")
    return value
