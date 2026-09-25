"""Validated host snapshot and advisory report types; stdlib only."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

MAX_SNAPSHOT_BYTES = 16_384
MAX_SUMMARY_CHARS = 1_000
MAX_OPTIONS = 16
MAX_OPTION_CHARS = 64
MIN_ADVISORY_CONFIDENCE = 0.55
MAX_REPORT_TTL_SECONDS = 300


class InputError(ValueError):
    """A caller payload is malformed, excessive, or unsupported."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_timestamp(value: str, field: str) -> datetime:
    if type(value) is not str or not value.strip():
        raise InputError(f"{field} must be a non-empty RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InputError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise InputError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def context_digest(context_id: str) -> str:
    return hashlib.sha256(context_id.encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise InputError(f"{field} must be non-empty and at most {maximum} characters")
    return value.strip()


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_OPTIONS:
        raise InputError(f"{field} must be a list with at most {MAX_OPTIONS} items")
    checked = [
        _bounded_text(item, f"{field} item", MAX_OPTION_CHARS) for item in value
    ]
    if not allow_empty and not checked:
        raise InputError(f"{field} must contain at least one explicit option")
    if len(set(checked)) != len(checked):
        raise InputError(f"{field} must contain unique options")
    return checked


@dataclass(frozen=True, slots=True)
class HostOption:
    id: str
    description: str

    @classmethod
    def from_mapping(cls, value: Any, field: str) -> HostOption:
        if not isinstance(value, Mapping) or set(value) != {"id", "description"}:
            raise InputError(f"{field} items require id and description")
        return cls(
            _bounded_text(value["id"], f"{field}.id", MAX_OPTION_CHARS),
            _bounded_text(
                value["description"], f"{field}.description", 240
            ),
        )


def _host_options(value: Any, field: str) -> list[HostOption]:
    if not isinstance(value, list) or len(value) > MAX_OPTIONS:
        raise InputError(f"{field} must be a list with at most {MAX_OPTIONS} items")
    checked = [HostOption.from_mapping(item, field) for item in value]
    if len({item.id for item in checked}) != len(checked):
        raise InputError(f"{field} must contain unique option ids")
    return checked


@dataclass(frozen=True, slots=True)
class HostSnapshot:
    context_id: str
    captured_at: str
    task_summary: str
    phase: str
    pending_action_count: int
    recent_failure_count: int
    context_tokens_used: int | None
    context_token_capacity: int | None
    checkpoint_ready: bool | None
    available_difficulties: list[HostOption]
    available_reasoning_efforts: list[HostOption]
    available_skills: list[HostOption]
    available_compaction_actions: list[HostOption]
    supported_host_actions: list[str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> HostSnapshot:
        if not isinstance(value, Mapping):
            raise InputError("snapshot must be a JSON object")
        try:
            encoded = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode()
        except (TypeError, ValueError) as error:
            raise InputError("snapshot must contain only JSON values") from error
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise InputError(f"snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes")
        expected = set(cls.__dataclass_fields__)
        unknown = set(value) - expected
        missing = expected - set(value)
        if unknown or missing:
            raise InputError(
                f"snapshot fields mismatch: missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        counts: dict[str, int] = {}
        for field in ("pending_action_count", "recent_failure_count"):
            item = value[field]
            if type(item) is not int or not 0 <= item <= 100:
                raise InputError(f"{field} must be an integer from 0 to 100")
            counts[field] = item
        context_values: dict[str, int | None] = {}
        for field in ("context_tokens_used", "context_token_capacity"):
            item = value[field]
            if item is not None and (type(item) is not int or not 0 <= item <= 100_000_000):
                raise InputError(f"{field} must be null or a bounded nonnegative integer")
            context_values[field] = item
        if (
            context_values["context_tokens_used"] is not None
            and context_values["context_token_capacity"] is not None
            and context_values["context_tokens_used"]
            > context_values["context_token_capacity"]
        ):
            raise InputError("context_tokens_used cannot exceed context_token_capacity")
        checkpoint_ready = value["checkpoint_ready"]
        if checkpoint_ready is not None and type(checkpoint_ready) is not bool:
            raise InputError("checkpoint_ready must be true, false, or null")
        captured_at = _bounded_text(value["captured_at"], "captured_at", 40)
        parse_timestamp(captured_at, "captured_at")
        return cls(
            context_id=_bounded_text(value["context_id"], "context_id", 128),
            captured_at=captured_at,
            task_summary=_bounded_text(
                value["task_summary"], "task_summary", MAX_SUMMARY_CHARS
            ),
            phase=_bounded_text(value["phase"], "phase", MAX_OPTION_CHARS),
            pending_action_count=counts["pending_action_count"],
            recent_failure_count=counts["recent_failure_count"],
            context_tokens_used=context_values["context_tokens_used"],
            context_token_capacity=context_values["context_token_capacity"],
            checkpoint_ready=checkpoint_ready,
            available_difficulties=_host_options(
                value["available_difficulties"], "available_difficulties"
            ),
            available_reasoning_efforts=_host_options(
                value["available_reasoning_efforts"], "available_reasoning_efforts"
            ),
            available_skills=_host_options(
                value["available_skills"], "available_skills"
            ),
            available_compaction_actions=_host_options(
                value["available_compaction_actions"],
                "available_compaction_actions",
            ),
            supported_host_actions=_strings(
                value["supported_host_actions"],
                "supported_host_actions",
                allow_empty=True,
            ),
        )

    def age_seconds(self, now: datetime) -> float:
        return (now - parse_timestamp(self.captured_at, "captured_at")).total_seconds()

    def validated_copy(self) -> HostSnapshot:
        """Revalidate direct dataclass construction at each public API boundary."""

        return type(self).from_mapping(asdict(self))


@dataclass(frozen=True, slots=True)
class AdvisoryRecommendation:
    difficulty: str
    reasoning_effort: str
    skill: str
    compaction: str
    confidence: float

    def __post_init__(self) -> None:
        for field in ("difficulty", "reasoning_effort", "skill", "compaction"):
            _bounded_text(getattr(self, field), field, MAX_OPTION_CHARS)
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise InputError("confidence must be between zero and one")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdviceReport:
    schema_version: str
    status: str
    generated_at: str
    expires_at: str
    context_digest: str
    recommendation: AdvisoryRecommendation | None
    provenance: Mapping[str, Any]
    usage: Mapping[str, Any]
    guidance: Mapping[str, str]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "generatedAt": self.generated_at,
            "expiresAt": self.expires_at,
            "contextDigest": self.context_digest,
            "recommendation": (
                None if self.recommendation is None else self.recommendation.to_dict()
            ),
            "provenance": dict(self.provenance),
            "usage": dict(self.usage),
            "guidance": dict(self.guidance),
            "reasons": list(self.reasons),
        }
