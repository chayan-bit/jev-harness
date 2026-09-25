"""In-memory reference implementations of the ports in ``jev_harness.ports``.

They are single-process and non-durable: suitable for tests, examples and as a
behavioural reference when writing a database-backed adapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .ports import ScopeGrant, StoredDecision
from .records import AdviceV2, BudgetExceeded, ContractError, DecisionRequest, IdempotencyConflict


@dataclass
class _Scope:
    grant: ScopeGrant
    evidence: dict[str, bytes] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    required_checks: set[str] = field(default_factory=set)


class InMemoryScope:
    """A ``DecisionScope`` holding grants, evidence artifacts and exact-check results in dictionaries."""

    def __init__(self) -> None:
        self._scopes: dict[str, _Scope] = {}

    def open_scope(self, scope: str, *, project_id: str, ttl_seconds: float = 3600) -> None:
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        self._scopes[scope] = _Scope(ScopeGrant(project_id, expires))

    def close_scope(self, scope: str) -> None:
        self._scopes.pop(scope, None)

    def add_evidence(self, scope: str, data: bytes) -> tuple[str, str]:
        """Store bytes as a new artifact; return ``(artifact_id, sha256)`` for a ``SubjectBinding``."""
        artifact_id = str(uuid4())
        self._state(scope).evidence[artifact_id] = bytes(data)
        return artifact_id, hashlib.sha256(data).hexdigest()

    def record_check(self, scope: str, check_profile_id: str, outcome: str, *, required: bool = True) -> None:
        state = self._state(scope)
        state.checks.append({"check_profile_id": check_profile_id, "outcome": outcome})
        if required:
            state.required_checks.add(check_profile_id)

    def _state(self, scope: str) -> _Scope:
        state = self._scopes.get(scope)
        if state is None:
            raise ContractError("scope does not admit advice")
        return state

    def admit(self, scope: str) -> ScopeGrant:
        return self._state(scope).grant

    def read_evidence(self, scope: str, artifact_id: str) -> bytes | None:
        state = self._scopes.get(scope)
        return None if state is None else state.evidence.get(artifact_id)

    def exact_checks(self, scope: str) -> Sequence[Mapping[str, Any]]:
        return [dict(check) for check in self._state(scope).checks]

    def required_exact_check_failed(self, scope: str) -> bool:
        state = self._state(scope)
        latest = {check["check_profile_id"]: check["outcome"] for check in state.checks}
        return any(latest.get(profile) in ("fail", "error") for profile in state.required_checks)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    scope: str
    kind: str
    payload: Mapping[str, Any]


class InMemoryDecisionStore:
    """A ``DecisionStore`` with dictionaries for decisions and cache and a list for audit events."""

    def __init__(self) -> None:
        self.decisions: dict[str, tuple[str, StoredDecision]] = {}
        self.cache: dict[str, AdviceV2] = {}
        self.events: list[AuditEvent] = []

    def load(self, decision_id: str) -> StoredDecision | None:
        entry = self.decisions.get(decision_id)
        return None if entry is None else entry[1]

    def begin(self, scope: str, request: DecisionRequest, request_digest: str, placeholder: AdviceV2) -> None:
        if request.decision_id in self.decisions:
            raise IdempotencyConflict("decision already exists")
        self.decisions[request.decision_id] = (scope, StoredDecision(request_digest, placeholder))

    def finish(self, scope: str, advice: AdviceV2, *, cache_key: str | None) -> None:
        decision_id = advice.outcome.decision_id
        stored_scope, stored = self.decisions[decision_id]
        if stored_scope != scope:
            raise ContractError("decision scope mismatch")
        self.decisions[decision_id] = (scope, StoredDecision(stored.request_digest, advice))
        if cache_key is not None:
            self.cache[cache_key] = advice
        self.events.append(AuditEvent(scope, "decision." + advice.outcome.status,
                                      {"decision_id": decision_id, "family": advice.family, "authority": "shadow"}))

    def cached(self, cache_key: str) -> AdviceV2 | None:
        return self.cache.get(cache_key)


@dataclass
class QuestionCall:
    scope: str
    decision_id: str
    state: str
    usage: Mapping[str, Any] | None = None


class InMemoryAccountant:
    """A ``JevAccountant`` with a per-scope question allowance and a call ledger."""

    def __init__(self, *, max_questions: int = 10, timeout_seconds: float = 8.0) -> None:
        if max_questions < 0 or timeout_seconds <= 0:
            raise ValueError("allowance must be nonnegative and timeout positive")
        self.max_questions = max_questions
        self._timeout = timeout_seconds
        self.spent: dict[str, int] = {}
        self.calls: dict[str, QuestionCall] = {}
        self.closed: set[str] = set()

    def close(self, scope: str) -> None:
        self.closed.add(scope)

    def admission_open(self, scope: str) -> bool:
        return scope not in self.closed

    def timeout_seconds(self, scope: str) -> float:
        return self._timeout

    def reserve_question(self, scope: str, decision_id: str) -> str:
        if not self.admission_open(scope) or self.spent.get(scope, 0) + 1 > self.max_questions:
            raise BudgetExceeded("Jev question allowance exhausted")
        self.spent[scope] = self.spent.get(scope, 0) + 1
        call_id = str(uuid4())
        self.calls[call_id] = QuestionCall(scope, decision_id, "intent")
        return call_id

    def settle_question(self, scope: str, call_id: str, usage: Mapping[str, Any] | None) -> None:
        call = self.calls[call_id]
        call.state = "unknown" if usage is None else "observed"
        call.usage = None if usage is None else dict(usage)
