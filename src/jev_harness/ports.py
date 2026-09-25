"""Typed ports a host harness implements to attach Jev shadow judgments.

A port is the only way ``Decisions`` touches host state.
Each port has an in-memory reference implementation in ``jev_harness.memory``.
All methods are synchronous; a host backed by a database should make each
method one transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .records import AdviceV2, DecisionCandidate, DecisionRequest


@dataclass(frozen=True, slots=True)
class ScopeGrant:
    """What the host currently allows inside one decision scope.

    ``project_id`` restricts candidates to sources from that project.
    ``expires_at`` must be timezone-aware; advice is refused once it passes.
    """

    project_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredDecision:
    """A persisted decision: the digest of the request that created it and its latest advice."""

    request_digest: str
    advice: AdviceV2


@runtime_checkable
class DecisionScope(Protocol):
    """Host authority and evidence for one scope (for example one objective or session)."""

    def admit(self, scope: str) -> ScopeGrant:
        """Return the live grant for ``scope`` or raise ``ContractError`` when advice is not admitted."""
        ...

    def read_evidence(self, scope: str, artifact_id: str) -> bytes | None:
        """Return the exact bytes of one evidence artifact inside ``scope``, or None when unavailable."""
        ...

    def exact_checks(self, scope: str) -> Sequence[Mapping[str, Any]]:
        """Return recorded exact-check results (JSON objects) in order; used by semantic-criterion judgments."""
        ...

    def required_exact_check_failed(self, scope: str) -> bool:
        """Return True when the latest result of any required exact check failed or errored."""
        ...


@runtime_checkable
class DecisionStore(Protocol):
    """Durable, idempotent decision records, an accepted-advice cache and an audit trail."""

    def load(self, decision_id: str) -> StoredDecision | None:
        """Return the stored decision or None."""
        ...

    def begin(self, scope: str, request: DecisionRequest, request_digest: str, placeholder: AdviceV2) -> None:
        """Insert a pending decision; must raise when ``request.decision_id`` already exists."""
        ...

    def finish(self, scope: str, advice: AdviceV2, *, cache_key: str | None) -> None:
        """Atomically replace the decision's advice, cache it when ``cache_key`` is set, and append an audit event."""
        ...

    def cached(self, cache_key: str) -> AdviceV2 | None:
        """Return previously accepted advice for an identical policy, model, scope, evidence and candidate set."""
        ...


@runtime_checkable
class JevAccountant(Protocol):
    """Budget admission and usage accounting for Jev questions."""

    def admission_open(self, scope: str) -> bool:
        """Return False once the scope's budget or clock no longer admits decisions."""
        ...

    def timeout_seconds(self, scope: str) -> float:
        """Return the maximum seconds one Jev evaluation may take in ``scope``."""
        ...

    def reserve_question(self, scope: str, decision_id: str) -> str:
        """Reserve one Jev question before dispatch and return a call id; raise ``BudgetExceeded`` when exhausted."""
        ...

    def settle_question(self, scope: str, call_id: str, usage: Mapping[str, Any] | None) -> None:
        """Record observed usage for a reserved call, or ``None`` when the outcome or usage is unknown."""
        ...


CandidateCatalog = Callable[[str, str], Iterable[DecisionCandidate]]
"""Trusted host callback ``(scope, family) -> eligible candidates``; never fed by worker input."""
