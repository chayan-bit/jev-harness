"""Strict, immutable records for Jev shadow judgments and offline evaluation.

Records are deeply immutable (tuples and frozen models) and reject unknown fields.
Use ``parse_record`` at every untrusted boundary, including direct Python callers.
None of these records carries execution or permission authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

MAX_BODY_BYTES = 262_144
MAX_JEV_BYTES = 16_384


class ContractError(ValueError):
    """A record, request or port result is malformed or violates a contract."""

    code = "INVALID_CONTRACT"

    def __init__(self, message: str = "invalid contract") -> None:
        super().__init__(message)


class UnsupportedCapability(ContractError):
    code = "UNSUPPORTED_CAPABILITY"


class IdempotencyConflict(ContractError):
    code = "IDEMPOTENCY_CONFLICT"


class PermissionDenied(ContractError):
    code = "PERMISSION_DENIED"


class BudgetExceeded(ContractError):
    code = "BUDGET_EXCEEDED"


def _json_value(value: Any, depth: int = 0) -> Any:
    if depth > 48:
        raise ContractError("JSON nesting exceeds limit")
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError("non-finite number")
        return value
    if type(value) is str:
        if "\0" in value:
            raise ContractError("NUL in string")
        value.encode("utf-8")
        return value
    if type(value) in (list, tuple):
        return [_json_value(v, depth + 1) for v in value]
    if type(value) is dict:
        if not all(type(k) is str for k in value):
            raise ContractError("JSON object keys must be strings")
        return {k: _json_value(v, depth + 1) for k, v in value.items()}
    raise ContractError("not a JSON value")


def canonical_json(value: Any) -> bytes:
    """Encode a JSON value deterministically (sorted keys, no whitespace, UTF-8)."""
    try:
        return json.dumps(_json_value(value), ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (UnicodeError, RecursionError, TypeError, ValueError) as error:
        raise ContractError("invalid canonical JSON") from error


def digest(value: Any) -> str:
    """SHA-256 hex digest of the canonical JSON encoding of ``value``."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _unique(values: tuple[Any, ...]) -> tuple[Any, ...]:
    if len(set(values)) != len(values):
        raise ValueError("duplicate values")
    return values


def _bytes(limit: int) -> Any:
    def check(value: str) -> str:
        if "\0" in value or not 0 < len(value.encode("utf-8")) <= limit:
            raise ValueError("string byte length or NUL invalid")
        return value
    return check


def _uuid(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("expected canonical UUID4")
    return value


def _utc(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise ValueError("expected RFC3339 UTC timestamp")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Identifier = Annotated[str, AfterValidator(_uuid)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Summary = Annotated[str, AfterValidator(_bytes(4000))]
Opaque = Annotated[str, AfterValidator(_bytes(256))]
UTC = Annotated[str, AfterValidator(_utc)]
Count = Annotated[int, Field(ge=0, le=2**63 - 1)]
Positive = Annotated[int, Field(ge=1, le=2**63 - 1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Names = Annotated[tuple[Name, ...], Field(max_length=256), AfterValidator(_unique)]
Ids = Annotated[tuple[Identifier, ...], Field(max_length=256), AfterValidator(_unique)]
AdviceSource = Literal["deterministic", "jev", "cache", "fallback"]
Family = Literal["jd-01", "jd-02", "jd-03", "jd-04", "jd-05", "jd-06", "jd-07"]


def _immutable_arrays(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_immutable_arrays(v) for v in value)
    if isinstance(value, dict):
        return {k: _immutable_arrays(v) for k, v in value.items()}
    return value


class Record(BaseModel):
    """Strict frozen base record: unknown fields, coercion and oversized bodies are refused."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always", validate_default=True,
                              hide_input_in_errors=True)
    schema_version: Literal[1] = 1

    @model_validator(mode="before")
    @classmethod
    def bound(cls, value: Any) -> Any:
        if isinstance(value, dict) and "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("schema version must be an integer")
        if len(canonical_json(value)) > MAX_BODY_BYTES:
            raise ValueError("record exceeds body limit")
        return _immutable_arrays(value)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key")
        result[key] = value
    return result


R = TypeVar("R", bound=Record)


def decode_json(value: Any, *, limit: int = MAX_BODY_BYTES) -> Any:
    """Decode a JSON document (or re-encode a Python value) with size and duplicate-key checks."""
    try:
        raw = value if isinstance(value, (str, bytes)) else canonical_json(value)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > limit:
            raise ContractError("body exceeds byte limit")
        decoded = json.loads(raw, object_pairs_hook=_object_pairs)
        canonical_json(decoded)
        return decoded
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ContractError("invalid JSON document") from error


def parse_record(cls: type[R], value: Any, *, limit: int = MAX_BODY_BYTES) -> R:
    """Validate before any access; detach even forged or mutated Python records."""
    try:
        return cls.model_validate_json(canonical_json(decode_json(value, limit=limit)))
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ContractError(f"invalid {cls.__name__}") from error


class SourceRecord(Record):
    """Where a candidate came from: a project id plus a revision and/or content digest."""

    project_id: Name
    revision: Opaque | None
    content_digest: Sha256 | None
    observed_at: UTC
    provenance_refs: Ids = ()

    @model_validator(mode="after")
    def source(self) -> SourceRecord:
        if self.revision is None and self.content_digest is None:
            raise ValueError("source needs revision or digest")
        return self


class DecisionCandidate(Record):
    candidate_id: Name
    description: Summary
    source: SourceRecord


class SubjectBinding(Record):
    """Binds a named judgment subject to one evidence artifact at an exact digest."""

    name: Name
    artifact_ref: Identifier
    source_digest: Sha256


class DecisionRequest(Record):
    decision_id: Identifier
    family: Name
    version: Positive
    subjects: tuple[SubjectBinding, ...]
    evidence_refs: Ids
    candidates: Annotated[tuple[DecisionCandidate, ...], Field(max_length=32)]
    scope: Identifier
    requested_model: Opaque
    policy_artifact_digest: Sha256
    deadline_at: UTC

    @model_validator(mode="after")
    def bindings(self) -> DecisionRequest:
        _unique(tuple(s.name for s in self.subjects))
        _unique(tuple(c.candidate_id for c in self.candidates))
        return self


class CandidateProbability(Record):
    candidate_id: Name | Literal["__jev_frame_no_fit__"]
    probability: Probability


class DecisionOutcome(Record):
    decision_id: Identifier
    status: Literal["accepted", "abstained", "no_fit", "unavailable", "stale"]
    selected_candidate: Name | None
    distribution: tuple[CandidateProbability, ...] = ()
    native_confidence: Probability | None
    evidence_digest: Sha256
    candidate_digest: Sha256
    requested_model: Opaque
    returned_model: Opaque | None
    policy_version: Positive
    usage_refs: Ids = ()
    expires_at: UTC

    @model_validator(mode="after")
    def selection(self) -> DecisionOutcome:
        if (self.status == "accepted") != (self.selected_candidate is not None):
            raise ValueError("selected candidate only for accepted advice")
        _unique(tuple(p.candidate_id for p in self.distribution))
        if self.distribution and abs(sum(p.probability for p in self.distribution) - 1) > 1e-6:
            raise ValueError("invalid probability distribution")
        return self


class DecisionPolicy(Record):
    """Frozen host registration for one decision family; always shadow mode."""

    family: Family
    version: Positive = 1
    model: Opaque = "jev-1.13.0"
    mode: Literal["shadow"] = "shadow"
    confidence_threshold: Probability | None = None
    deterministic_single: bool = True
    fallback_candidate: Name | None = None
    ttl_seconds: Annotated[int, Field(ge=1, le=300)] = 300


class AdviceV2(Record):
    """Shadow advice for one decision; ``execution_authority`` is always false."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    family: Name
    mode: Literal["shadow"] = "shadow"
    source: AdviceSource
    outcome: DecisionOutcome
    fallback_candidate: Name | None
    execution_authority: Literal[False] = False
    reason_codes: Names = ()


class CapturedDecision(Record):
    """One opt-in offline capture of a decision request and its observed outcome."""

    case_id: Name
    dataset_version: Opaque
    source_lineage: Opaque
    group_id: Name
    family: Name
    request: DecisionRequest
    outcome: DecisionOutcome
    actual_action: Name | None
    actual_outcome_observed: bool
    input_tokens: Count | None
    output_tokens: Count | None
    provider_attempts: Count
    usage_coverage: Literal["complete", "partial", "unknown"]
    evidence_level: Literal["offline_fixture", "live_observation"]
    seed: Count

    @model_validator(mode="after")
    def identities(self) -> CapturedDecision:
        if self.request.decision_id != self.outcome.decision_id or self.family != self.request.family:
            raise ValueError("capture identity mismatch")
        candidate_ids = {c.candidate_id for c in self.request.candidates}
        if self.outcome.selected_candidate is not None and self.outcome.selected_candidate not in candidate_ids:
            raise ValueError("capture candidate outside request")
        if (self.outcome.requested_model != self.request.requested_model
                or self.outcome.candidate_digest != digest(self.request.candidates)):
            raise ValueError("capture provenance mismatch")
        if self.actual_action is not None and self.actual_action not in candidate_ids:
            raise ValueError("actual action outside captured candidates")
        if self.usage_coverage == "complete" and (self.input_tokens is None or self.output_tokens is None):
            raise ValueError("complete capture needs observed token counters")
        return self


class DecisionLabel(Record):
    """Evaluator-only label for one captured case; never shown to the model."""

    case_id: Name
    acceptable_candidates: Names
    harmful_candidates: Names = ()
    acceptable_abstention: bool = False
    severe_violation: bool = False
    evaluator_version: Opaque
    provenance_digest: Sha256

    @model_validator(mode="after")
    def disjoint_labels(self) -> DecisionLabel:
        if set(self.acceptable_candidates) & set(self.harmful_candidates):
            raise ValueError("acceptable and harmful labels overlap")
        return self


class PilotRun(Record):
    """One paired pilot observation of a task under one arm and repetition."""

    task_id: Name
    group_id: Name
    family_id: Name
    arm: Name
    repetition: Positive
    seed: Count
    permission_digest: Sha256
    resource_digest: Sha256
    source_digest: Sha256
    verified_complete: bool
    harmful_actions: Count
    intervention_ids: Ids = ()
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    jev_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cost_micro_usd: Count | None
    jev_cost_micro_usd: Count | None
    usage_coverage: Literal["complete", "partial", "unknown"]
    recovery_succeeded: bool | None = None
    unnecessary_delegations: Count = 0
    repeated_failures: Count = 0
    context_losses: Count = 0
    evidence_level: Literal["offline_fixture", "live_observation"]
    jev_questions: Count = 0
    jev_accepted: Count = 0
    jev_wrong: Count = 0

    @model_validator(mode="after")
    def accounting_consistency(self) -> PilotRun:
        if not self.jev_wrong <= self.jev_accepted <= self.jev_questions:
            raise ValueError("inconsistent Jev observation counts")
        if self.usage_coverage == "complete" and (self.cost_micro_usd is None or self.jev_cost_micro_usd is None):
            raise ValueError("complete pilot accounting requires both costs")
        return self

    @property
    def uses_jev(self) -> bool:
        return bool(self.jev_seconds or self.jev_cost_micro_usd not in (0, None) or self.jev_questions)
