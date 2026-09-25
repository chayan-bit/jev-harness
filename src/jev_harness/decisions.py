"""Independent, source-bound shadow judgments through the Jev-Frame runtime.

A decision never chooses an execution target by itself: every result is shadow
advice with ``execution_authority`` false.  All host state flows through the
ports in ``jev_harness.ports``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jev_frame import (
    AgentDefinition,
    Candidate,
    CandidateBinding,
    CandidateProvider,
    CandidateSet,
    CapabilityPackage,
    ChoiceAnswer,
    ChoiceQuestion,
    CompletionContract,
    Coverage,
    HostContextBinding,
    Judgment,
    JudgmentBinding,
    OperatingPolicy,
    ProviderBatch,
    RunContext,
    RunLimits,
    Runtime,
    Subject,
    TaskInputBinding,
    TerminalStatus,
    Tool,
    TypeSafeProvider,
)

from .ports import CandidateCatalog, DecisionScope, DecisionStore, JevAccountant
from .records import (
    MAX_JEV_BYTES,
    AdviceSource,
    AdviceV2,
    BudgetExceeded,
    CandidateProbability,
    ContractError,
    DecisionCandidate,
    DecisionOutcome,
    DecisionPolicy,
    DecisionRequest,
    IdempotencyConflict,
    PermissionDenied,
    UnsupportedCapability,
    canonical_json,
    digest,
    parse_record,
)

FAMILIES = {
    "jd-01": "Select an approved worker profile suitable for the bounded task and supplied capabilities.",
    "jd-02": "Select an installed compatible optional skill relevant to the task, or none. Preserve explicit user skill requests.",
    "jd-03": "Classify the sanitized failure; missing facts warrant unknown rather than a guessed diagnosis.",
    "jd-04": "Assess whether one additional review would help. Never remove a required final independent review.",
    "jd-05": "Select the supplied evidence most relevant to the explicitly bound question.",
    "jd-06": "Assess only the narrow semantic criterion at the supplied artifact revision. Advice cannot override exact checks.",
    "jd-07": "Advise a context transition using known telemetry and validated checkpoint evidence; never infer capacity from account quota.",
}
FIXED_CANDIDATES = {
    "jd-03": {"transient", "environment", "implementation", "missing_evidence", "scope_block", "unknown"},
    "jd-04": {"review", "no_extra_review", "need_evidence"},
    "jd-07": {"keep", "checkpoint_then_compact", "new_session", "need_evidence"},
}
DEFAULT_FALLBACKS = {"jd-02": "none", "jd-03": "unknown", "jd-04": "need_evidence", "jd-07": "keep"}
SECRET_INDICATORS = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})"
)


def reject_secret_indicators(content: bytes) -> None:
    """Refuse bytes that look like a private key or a common API token before they are sent or stored."""
    if SECRET_INDICATORS.search(content):
        raise PermissionDenied("secret indicator rejected before ordinary persistence")


@dataclass(frozen=True)
class Option:
    candidate_id: str
    description: str
    source_id: str
    source_version: str


@dataclass(frozen=True)
class Projection:
    evidence: str
    options: list[Option]


@dataclass(frozen=True)
class Selection:
    candidate_id: str
    confidence: float


def _candidates(options: list[Option], scope: str) -> CandidateSet:
    return CandidateSet("options", "1.0.0", tuple(
        Candidate(c.candidate_id, c.candidate_id, c.description, source_id=c.source_id, source_version=c.source_version)
        for c in options), Coverage.COMPLETE, scope, total_count=len(options))


def _selection(selected: str, answer: ChoiceAnswer) -> Selection:
    return Selection(candidate_id=selected, confidence=answer.confidence)


def definition(family: str) -> AgentDefinition[Projection, Selection]:
    """Return the Jev-Frame agent definition for one registered decision family."""
    if family not in FAMILIES:
        raise UnsupportedCapability("decision family is not registered")
    package = CapabilityPackage(
        "supervisor-decisions", "2.0.0",
        judgments=(Judgment(id="selection", version="1.0.0", primitive=ChoiceQuestion(FAMILIES[family]),
                            subjects=(Subject("evidence"),), candidate_set="options"),),
        candidate_providers=(CandidateProvider(
            id="options", version="1.0.0", function=_candidates,
            bindings={"options": TaskInputBinding(("options",)), "scope": HostContextBinding("scope")}),),
        tools=(Tool(id="assemble", version="1.0.0", purpose="Record advisory selection without any external effect.",
                    function=_selection, bindings={"selected": CandidateBinding("options", "selection"),
                                                   "answer": JudgmentBinding("selection")},
                    produces_evidence=("advice",)),))
    return AgentDefinition(
        id="supervisor-" + family, version="2.0.0", objective=FAMILIES[family],
        input_type=Projection, output_type=Selection, completion=CompletionContract(("advice",), {}, "valid-advice"),
        policy=OperatingPolicy("1.0.0", ("valid-advice",)), packages=(package,))


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _context(projection: Mapping[str, Any], request: DecisionRequest) -> Any:
    try:
        return next((json.loads(projection["artifacts"][s.artifact_ref])
                     for s in request.subjects if s.name == "context"), {})
    except ValueError:
        return {}


@dataclass(frozen=True)
class _Current:
    policy: DecisionPolicy
    candidates: tuple[DecisionCandidate, ...]
    projection: dict[str, Any]
    evidence_digest: str
    candidate_digest: str
    cache_key: str


class Decisions:
    """Evaluate shadow judgments for registered families against host ports.

    ``policies`` and ``eligible_candidates`` are trusted host registrations, never worker input.
    ``provider`` is optional; without it every non-deterministic decision falls back.
    A live provider must declare ``qualified = True`` (see ``MeteredJevProvider``);
    offline doubles declare ``live = False``.  A raw ``TypeSafeProvider`` is refused.
    """

    def __init__(
        self,
        policies: Iterable[DecisionPolicy],
        eligible_candidates: CandidateCatalog,
        *,
        scope: DecisionScope,
        store: DecisionStore,
        accountant: JevAccountant,
        provider: Any = None,
    ) -> None:
        policies = tuple(policies)
        self.policies: dict[str, DecisionPolicy] = {p.family: parse_record(DecisionPolicy, p) for p in policies}
        if len(self.policies) != len(policies):
            raise ContractError("duplicate decision policy")
        self.eligible_candidates = eligible_candidates
        self.scope, self.store, self.accountant = scope, store, accountant
        if provider is not None and (
            isinstance(provider, TypeSafeProvider)
            or (getattr(provider, "live", True) is not False and getattr(provider, "qualified", False) is not True)
        ):
            raise UnsupportedCapability("native Jev evaluation needs a qualified provider or an explicit offline fixture")
        self.provider: Any = provider

    def _current(self, scope: str, request: DecisionRequest) -> _Current:
        policy = self.policies.get(request.family)
        if (policy is None or request.scope != scope or request.version != policy.version
                or request.requested_model != policy.model or request.policy_artifact_digest != digest(policy)):
            raise ContractError("decision scope or frozen policy mismatch")
        grant = self.scope.admit(scope)
        if grant.expires_at.tzinfo is None:
            raise ContractError("scope grant expiry must be timezone-aware")
        if min(_utc(request.deadline_at), grant.expires_at) <= datetime.now(UTC):
            raise ContractError("decision authority expired")
        if not self.accountant.admission_open(scope):
            raise BudgetExceeded("decision admission closed")
        catalog = tuple(parse_record(DecisionCandidate, c) for c in self.eligible_candidates(scope, request.family))
        if len({c.candidate_id for c in catalog}) != len(catalog):
            raise ContractError("duplicate eligible candidate")
        allowed = {c.candidate_id: c for c in catalog if c.source.project_id == grant.project_id}
        candidates = tuple(c for c in request.candidates if allowed.get(c.candidate_id) == c)
        if request.family in FIXED_CANDIDATES and any(
                c.candidate_id not in FIXED_CANDIDATES[request.family] for c in candidates):
            raise ContractError("candidate outside fixed family vocabulary")
        if request.family == "jd-02" and len(candidates) > 17:
            raise ContractError("skill family allows sixteen skills plus none")
        evidence: dict[str, str] = {}
        refs: dict[str, str] = {}
        for artifact_id in set(request.evidence_refs) | {s.artifact_ref for s in request.subjects}:
            raw = self.scope.read_evidence(scope, artifact_id)
            if raw is None:
                raise ContractError("decision evidence unavailable")
            if type(raw) is not bytes or len(raw) > MAX_JEV_BYTES:
                raise ContractError("project a bounded evidence artifact before evaluating")
            reject_secret_indicators(raw)
            refs[artifact_id] = hashlib.sha256(raw).hexdigest()
            evidence[artifact_id] = raw.decode("utf-8")
        for subject in request.subjects:
            if subject.source_digest != refs[subject.artifact_ref]:
                raise ContractError("subject evidence changed")
        projection: dict[str, Any] = {"subjects": [s.model_dump(mode="json") for s in request.subjects],
                                      "artifacts": evidence}
        if request.family == "jd-06":
            projection["exact_checks"] = [dict(check) for check in self.scope.exact_checks(scope)]
        if request.family == "jd-07":
            context = _context(projection, request)
            if not isinstance(context, dict) or context.get("checkpoint_ready") is not True:
                candidates = tuple(c for c in candidates if c.candidate_id != "checkpoint_then_compact")
            if (isinstance(context, dict) and type(context.get("context_tokens_used")) is int
                    and type(context.get("context_token_capacity")) is int and context["context_token_capacity"] > 0
                    and context["context_tokens_used"] / context["context_token_capacity"] >= 0.85):
                candidates = tuple(c for c in candidates if c.candidate_id != "keep")
        if len(canonical_json(projection)) > MAX_JEV_BYTES:
            raise ContractError("decision projection exceeds sixteen KiB")
        evidence_digest = digest(projection)
        candidate_digest = digest(candidates)
        key = digest({"family": policy.family, "policy": policy, "model": request.requested_model,
                      "scope": scope, "evidence": evidence_digest, "candidates": candidate_digest})
        return _Current(policy, candidates, projection, evidence_digest, candidate_digest, key)

    def _fallback(self, policy: DecisionPolicy, candidates: tuple[DecisionCandidate, ...]) -> str | None:
        if policy.family == "jd-06":
            return None
        fallback = policy.fallback_candidate or DEFAULT_FALLBACKS.get(policy.family)
        return fallback if fallback in {c.candidate_id for c in candidates} else None

    def _load(self, decision_id: str) -> tuple[str, AdviceV2] | None:
        stored = self.store.load(decision_id)
        if stored is None:
            return None
        return stored.request_digest, parse_record(AdviceV2, stored.advice)

    async def evaluate(self, scope: str, value: Any) -> AdviceV2:
        """Evaluate one decision request; idempotent per ``decision_id``."""
        request = parse_record(DecisionRequest, value, limit=MAX_JEV_BYTES)
        current = self._current(scope, request)
        policy, candidates = current.policy, current.candidates
        previous = self._load(request.decision_id)
        if previous:
            if previous[0] != digest(request):
                raise IdempotencyConflict("decision identity changed")
            advice = previous[1]
            if (advice.outcome.candidate_digest != current.candidate_digest
                    or advice.outcome.evidence_digest != current.evidence_digest
                    or _utc(advice.outcome.expires_at) <= datetime.now(UTC)):
                return self._stale(advice)
            return advice
        now = datetime.now(UTC)
        expires = min(_utc(request.deadline_at), now + timedelta(seconds=policy.ttl_seconds))
        outcome: dict[str, Any] = dict(
            decision_id=request.decision_id, status="unavailable", selected_candidate=None, native_confidence=None,
            evidence_digest=current.evidence_digest, candidate_digest=current.candidate_digest,
            requested_model=request.requested_model, returned_model=None, policy_version=policy.version,
            expires_at=expires.isoformat().replace("+00:00", "Z"))
        placeholder = AdviceV2(family=request.family, source="fallback", outcome=parse_record(DecisionOutcome, outcome),
                               fallback_candidate=self._fallback(policy, candidates), reason_codes=("evaluation_pending",))
        self.store.begin(scope, request, digest(request), placeholder)
        source: AdviceSource = "fallback"
        reasons: list[str] = []
        cached_value = self.store.cached(current.cache_key)
        cached = None if cached_value is None else parse_record(AdviceV2, cached_value)
        context_unknown = False
        if request.family == "jd-07":
            context = _context(current.projection, request)
            context_unknown = not (
                isinstance(context, dict) and type(context.get("context_tokens_used")) is int
                and type(context.get("context_token_capacity")) is int
                and 0 <= context["context_tokens_used"] <= context["context_token_capacity"]
                and context["context_token_capacity"] > 0 and type(context.get("checkpoint_ready")) is bool)
        if not candidates:
            outcome["status"] = "no_fit"
            reasons = ["no_permitted_candidate"]
        elif context_unknown:
            reasons = ["unknown_context_telemetry"]
        elif request.family == "jd-06" and self.scope.required_exact_check_failed(scope):
            outcome["status"] = "abstained"
            reasons = ["required_exact_check_failed"]
        elif len(candidates) == 1 and policy.deterministic_single and request.family != "jd-06":
            outcome.update(status="accepted", selected_candidate=candidates[0].candidate_id)
            source = "deterministic"
        elif cached and _utc(cached.outcome.expires_at) > datetime.now(UTC):
            outcome = cached.outcome.model_dump() | {"decision_id": request.decision_id}
            source = "cache"
        elif self.provider is None:
            reasons = ["provider_not_enabled"]
        else:
            outcome, reasons = await self._judge(scope, request, current, outcome)
            source = "jev"
            try:
                after = self._current(scope, request)
                changed = (after.evidence_digest, after.candidate_digest) != (
                    current.evidence_digest, current.candidate_digest)
            except ContractError:
                changed = True
            if changed:
                outcome.update(status="stale", selected_candidate=None)
                reasons = ["inputs_changed_during_evaluation"]
        advice = AdviceV2(family=request.family, source=source, outcome=parse_record(DecisionOutcome, outcome),
                          fallback_candidate=None if outcome["status"] == "stale" else self._fallback(policy, candidates),
                          reason_codes=tuple(reasons))
        self.store.finish(scope, advice, cache_key=current.cache_key if advice.outcome.status == "accepted" else None)
        return advice

    @staticmethod
    def _stale(advice: AdviceV2) -> AdviceV2:
        return parse_record(AdviceV2, advice.model_dump() | {
            "outcome": advice.outcome.model_dump() | {"status": "stale", "selected_candidate": None},
            "fallback_candidate": None, "reason_codes": ["stale_advice"]})

    def dispatch_candidate(self, scope: str, value: Any) -> str | None:
        """Revalidate now and return the host-safe candidate; shadow Jev judgments never choose it.

        Returns the deterministic single candidate, otherwise the policy fallback, otherwise None.
        """
        request = parse_record(DecisionRequest, value)
        current = self._current(scope, request)
        previous = self._load(request.decision_id)
        if not previous or previous[0] != digest(request):
            return None
        advice = previous[1]
        if (advice.outcome.status == "stale" or advice.outcome.candidate_digest != current.candidate_digest
                or advice.outcome.evidence_digest != current.evidence_digest
                or _utc(advice.outcome.expires_at) <= datetime.now(UTC)):
            return None
        if current.policy.family == "jd-06":
            return None
        if advice.source == "deterministic" and advice.outcome.status == "accepted":
            return advice.outcome.selected_candidate
        return self._fallback(current.policy, current.candidates)

    async def _judge(self, scope: str, request: DecisionRequest, current: _Current,
                     outcome: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        metered = _MeteredDispatch(self, scope, request, current)
        timeout = min(self.accountant.timeout_seconds(scope),
                      (_utc(request.deadline_at) - datetime.now(UTC)).total_seconds())
        if timeout <= 0:
            return outcome, ["decision_deadline"]
        runtime = Runtime(metered, model=request.requested_model,
                          completion_checks={"valid-advice": lambda value, _: True})
        options = [Option(c.candidate_id, c.description, c.source.project_id,
                          c.source.revision or c.source.content_digest or "")
                   for c in current.candidates]
        try:
            result = await asyncio.wait_for(runtime.run(
                definition(request.family),
                Projection(evidence=canonical_json(current.projection).decode(), options=options),
                RunContext(scope, time.monotonic() + timeout, RunLimits(1, 1, 2, 0, 1, 0, 0, 0, 0, 0),
                           clock=time.monotonic, run_id=request.decision_id)), timeout=timeout)
            outcome["usage_refs"] = list(metered.call_ids)
            outcome["returned_model"] = metered.batches[-1].returned_model if metered.batches else None
            if result.status is TerminalStatus.COMPLETED:
                selection = result.value
                if not isinstance(selection, Selection):
                    raise ContractError("completed run returned no selection")
                answer = next(iter(metered.batches[-1].answers.values()))
                probabilities = getattr(answer, "probabilities", {})
                outcome.update(native_confidence=selection.confidence, distribution=[
                    CandidateProbability(candidate_id=k, probability=v).model_dump() for k, v in probabilities.items()])
                if selection.candidate_id not in {c.candidate_id for c in current.candidates}:
                    return outcome | {"status": "stale"}, ["candidate_not_permitted"]
                threshold = current.policy.confidence_threshold
                if threshold is not None and selection.confidence >= threshold:
                    accepted = outcome | {"status": "accepted", "selected_candidate": selection.candidate_id}
                    parse_record(DecisionOutcome, accepted)
                    return accepted, []
                return outcome | {"status": "abstained"}, ["shadow_threshold_unset_or_not_met"]
            no_fit = any(item.reason.value == "no_fit" for item in result.unresolved)
            return outcome | {"status": "no_fit" if no_fit else "abstained"}, ["jev_unresolved"]
        except Exception:
            outcome["usage_refs"] = list(metered.call_ids)
            return outcome | {"status": "unavailable", "selected_candidate": None, "distribution": [],
                              "native_confidence": None}, ["jev_unavailable"]


class _MeteredDispatch:
    """Provider wrapper: one attempt per decision, revalidated and budgeted before it leaves the host."""

    def __init__(self, owner: Decisions, scope: str, request: DecisionRequest, current: _Current) -> None:
        self.owner, self.scope, self.request, self.current = owner, scope, request, current
        self.batches: list[ProviderBatch] = []
        self.call_ids: list[str] = []
        self.called = False

    async def evaluate(self, **kwargs: Any) -> ProviderBatch:
        if self.called:
            raise ContractError("one transport attempt per decision")
        self.called = True
        owner, request = self.owner, self.request
        latest = owner._current(self.scope, request)
        if (latest.evidence_digest, latest.candidate_digest) != (
                self.current.evidence_digest, self.current.candidate_digest):
            raise ContractError("decision inputs changed before dispatch")
        call_id = owner.accountant.reserve_question(self.scope, request.decision_id)
        self.call_ids.append(call_id)
        try:
            batch: ProviderBatch = await owner.provider.evaluate(**kwargs)
            self.batches.append(batch)
            usage = batch.usage
            if (batch.requested_model != request.requested_model or batch.returned_model != request.requested_model
                    or len(batch.attempts) != 1 or usage.provider_attempts != 1 or usage.submitted_questions != 1):
                raise ContractError("provider response model or attempt accounting mismatch")
            if any(v is not None and (type(v) is not int or v < 0) for v in (usage.input_tokens, usage.output_tokens)):
                raise ContractError("invalid normalized Jev usage")
        except BaseException:
            owner.accountant.settle_question(self.scope, call_id, None)
            raise
        owner.accountant.settle_question(self.scope, call_id, {
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "provider_attempts": 1, "submitted_questions": 1, "coverage": usage.coverage.value})
        return batch
