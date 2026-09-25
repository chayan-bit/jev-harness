import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from jev_frame import ProviderBatch, TypeSafeProvider

from jev_harness import (
    AdviceV2,
    ContractError,
    DecisionCandidate,
    DecisionPolicy,
    DecisionRequest,
    Decisions,
    IdempotencyConflict,
    InMemoryAccountant,
    InMemoryDecisionStore,
    InMemoryScope,
    MeteredJevProvider,
    OfflineChoiceProvider,
    SourceRecord,
    SubjectBinding,
    UnsupportedCapability,
    canonical_json,
    definition,
    digest,
    parse_record,
)
from jev_harness.decisions import FAMILIES

PROJECT = "sample-repo"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class OfflineProvider(OfflineChoiceProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.before_return = None

    async def evaluate(self, **kwargs):
        result = await super().evaluate(**kwargs)
        if self.before_return:
            self.before_return()
        return ProviderBatch(result.answers, result.requested_model, self.model, result.request_id,
                             result.usage, result.attempts)


def candidates(*names):
    return tuple(DecisionCandidate(candidate_id=name, description="Approved synthetic option " + name,
                                   source=SourceRecord(project_id=PROJECT, revision="fixture-v1",
                                                       content_digest=digest(name), observed_at=utc_now()))
                 for name in names)


def request(scope, policy, options, subjects=()):
    return DecisionRequest(decision_id=str(uuid4()), family=policy.family, version=policy.version,
                           subjects=tuple(subjects), evidence_refs=(), candidates=options, scope=scope,
                           requested_model=policy.model, policy_artifact_digest=digest(policy),
                           deadline_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"))


def harness(policies, catalog, provider=None, *, max_questions=10):
    scope, store, accountant = InMemoryScope(), InMemoryDecisionStore(), InMemoryAccountant(max_questions=max_questions)
    oid = str(uuid4())
    scope.open_scope(oid, project_id=PROJECT)
    engine = Decisions(policies, catalog, scope=scope, store=store, accountant=accountant, provider=provider)
    return SimpleNamespace(oid=oid, scope=scope, store=store, accountant=accountant, engine=engine)


def evidence(h, value):
    artifact_id, sha256 = h.scope.add_evidence(h.oid, canonical_json(value))
    return artifact_id, sha256


def states(h):
    return [call.state for call in h.accountant.calls.values()]


class DecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_definitions_and_zero_one_candidate_do_not_call_provider(self):
        self.assertEqual(len({definition(f).id for f in FAMILIES}), 7)
        provider = OfflineProvider()
        policy = DecisionPolicy(family="jd-01", fallback_candidate="small")
        options = candidates("small")
        h = harness((policy,), lambda *_: options, provider)
        none = await h.engine.evaluate(h.oid, request(h.oid, policy, ()))
        one = await h.engine.evaluate(h.oid, request(h.oid, policy, options))
        self.assertEqual(none.outcome.status, "no_fit")
        self.assertEqual(one.outcome.selected_candidate, "small")
        self.assertEqual(one.source, "deterministic")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(h.accountant.calls, {})
        self.assertFalse(one.execution_authority)

    async def test_runtime_independent_advice_shadow_fallback_and_cache(self):
        provider = OfflineProvider()
        policy = DecisionPolicy(family="jd-02", confidence_threshold=0.8, fallback_candidate="none")
        options = candidates("test-runner", "none")
        h = harness((policy,), lambda *_: options, provider)
        first = request(h.oid, policy, options)
        advice = await h.engine.evaluate(h.oid, first)
        self.assertEqual(advice.outcome.status, "accepted", advice)
        self.assertEqual(advice.outcome.selected_candidate, "test-runner")
        self.assertEqual(advice.mode, "shadow")
        self.assertEqual(h.engine.dispatch_candidate(h.oid, first), "none")
        self.assertEqual(parse_record(AdviceV2, advice.model_dump_json()), advice)
        self.assertEqual((await h.engine.evaluate(h.oid, request(h.oid, policy, options))).source, "cache")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(h.accountant.spent[h.oid], 1)
        self.assertEqual(len(provider.questions), 1)
        self.assertEqual(states(h), ["observed"])
        self.assertEqual(advice.outcome.usage_refs, tuple(h.accountant.calls))
        self.assertEqual([e.kind for e in h.store.events], ["decision.accepted", "decision.accepted"])

    async def test_outage_low_confidence_no_fit_and_unset_threshold_never_accept(self):
        for provider in (OfflineProvider(fail=True), OfflineProvider(confidence=0.1),
                         OfflineProvider(no_fit=True), OfflineProvider()):
            with self.subTest(provider=provider):
                policy = DecisionPolicy(family="jd-03")
                options = candidates("implementation", "unknown")
                h = harness((policy,), lambda *_, o=options: o, provider)
                req = request(h.oid, policy, options)
                advice = await h.engine.evaluate(h.oid, req)
                self.assertNotEqual(advice.outcome.status, "accepted")
                self.assertEqual(h.engine.dispatch_candidate(h.oid, req), "unknown")
                self.assertEqual(h.store.cache, {})
                self.assertEqual(provider.calls, 1)

    async def test_unknown_compaction_telemetry_does_not_block_skill_family(self):
        context_policy, skills = DecisionPolicy(family="jd-07"), DecisionPolicy(family="jd-02", confidence_threshold=0.7)
        catalogs = {"jd-07": candidates("keep", "new_session"), "jd-02": candidates("none", "test-runner")}
        provider = OfflineProvider()
        h = harness((context_policy, skills), lambda _, f: catalogs[f], provider)
        artifact_id, sha256 = evidence(h, {"context_tokens_used": None, "context_token_capacity": None,
                                           "checkpoint_ready": False})
        subject = SubjectBinding(name="context", artifact_ref=artifact_id, source_digest=sha256)
        results = await asyncio.gather(
            h.engine.evaluate(h.oid, request(h.oid, context_policy, catalogs["jd-07"], (subject,))),
            h.engine.evaluate(h.oid, request(h.oid, skills, catalogs["jd-02"])))
        self.assertEqual(results[0].reason_codes, ("unknown_context_telemetry",))
        self.assertEqual(results[1].outcome.status, "accepted")
        self.assertEqual(provider.calls, 1)

    async def test_candidate_removed_during_call_or_before_dispatch_is_stale(self):
        policy = DecisionPolicy(family="jd-01", confidence_threshold=0.8, fallback_candidate="small")
        catalog = list(candidates("large", "small"))
        provider = OfflineProvider()
        h = harness((policy,), lambda *_: tuple(catalog), provider)
        req = request(h.oid, policy, tuple(catalog))
        provider.before_return = lambda: catalog.pop(0)
        advice = await h.engine.evaluate(h.oid, req)
        self.assertEqual(advice.outcome.status, "stale")
        self.assertIsNone(h.engine.dispatch_candidate(h.oid, req))
        provider.before_return = None
        req2 = request(h.oid, policy, tuple(catalog))
        self.assertEqual((await h.engine.evaluate(h.oid, req2)).source, "deterministic")
        catalog.clear()
        self.assertIsNone(h.engine.dispatch_candidate(h.oid, req2))
        self.assertEqual((await h.engine.evaluate(h.oid, req2)).outcome.status, "stale")

    async def test_failed_exact_check_wins_and_dependent_stages_are_separate_decisions(self):
        policy = DecisionPolicy(family="jd-06", confidence_threshold=0.5)
        options = candidates("satisfies", "need_evidence")
        provider = OfflineProvider(confidence=1.0)
        h = harness((policy,), lambda *_: options, provider)
        first_request = request(h.oid, policy, options)
        first = await h.engine.evaluate(h.oid, first_request)
        self.assertEqual(first.outcome.status, "accepted")
        # The next stage explicitly receives persisted evidence from the preceding result.
        prior_id, prior_sha = evidence(h, first.model_dump(mode="json"))
        second = await h.engine.evaluate(h.oid, request(h.oid, policy, options, (
            SubjectBinding(name="prior_stage", artifact_ref=prior_id, source_digest=prior_sha),)))
        self.assertEqual(second.outcome.status, "accepted")
        self.assertEqual(provider.calls, 2)
        h.scope.record_check(h.oid, "offline-integrity", "fail")
        self.assertEqual((await h.engine.evaluate(h.oid, first_request)).outcome.status, "stale")
        failed = await h.engine.evaluate(h.oid, request(h.oid, policy, options))
        self.assertEqual(failed.reason_codes, ("required_exact_check_failed",))
        self.assertIsNone(failed.fallback_candidate)
        self.assertEqual(provider.calls, 2)

    async def test_limits_identity_model_binding_and_native_provider_denial(self):
        policy = DecisionPolicy(family="jd-01", confidence_threshold=0.8)
        options = candidates("a", "b")
        provider = OfflineProvider()
        h = harness((policy,), lambda *_: options, provider)
        req = request(h.oid, policy, options)
        await h.engine.evaluate(h.oid, req)
        with self.assertRaises(IdempotencyConflict):
            await h.engine.evaluate(h.oid, parse_record(DecisionRequest, req.model_dump() | {"candidates": []}))
        with self.assertRaises(UnsupportedCapability):
            harness((policy,), lambda *_: options, type("Unqualified", (), {})())
        provider.model = "different-model"
        altered = candidates("c", "d")
        h.engine.eligible_candidates = lambda *_: altered
        bad = await h.engine.evaluate(h.oid, request(h.oid, policy, altered))
        self.assertNotEqual(bad.outcome.status, "accepted")
        self.assertEqual(states(h).count("unknown"), 1)
        h.accountant.max_questions = 2
        exhausted = await h.engine.evaluate(h.oid, request(h.oid, policy, altered))
        self.assertNotEqual(exhausted.outcome.status, "accepted")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(h.accountant.calls), 2)

    async def test_same_decision_in_flight_is_not_dispatched_twice_and_sources_are_exact(self):
        started, release = asyncio.Event(), asyncio.Event()

        class Paused(OfflineProvider):
            async def evaluate(self, **kwargs):
                started.set()
                await release.wait()
                return await super().evaluate(**kwargs)

        provider = Paused()
        policy = DecisionPolicy(family="jd-05", confidence_threshold=0.8, fallback_candidate="source_a")
        options = candidates("source_a", "source_b")
        h = harness((policy,), lambda *_: options, provider)
        artifact_id, sha256 = evidence(h, {"question": "Which source supports the narrow claim?"})
        subject = SubjectBinding(name="question", artifact_ref=artifact_id, source_digest=sha256)
        req = request(h.oid, policy, options, (subject,))
        task = asyncio.create_task(h.engine.evaluate(h.oid, req))
        await asyncio.wait_for(started.wait(), timeout=2)
        pending = await h.engine.evaluate(h.oid, req)
        self.assertEqual(pending.reason_codes, ("evaluation_pending",))
        self.assertEqual(len(h.accountant.calls), 1)
        release.set()
        self.assertEqual((await task).outcome.status, "accepted")
        self.assertEqual(provider.calls, 1)
        wrong = parse_record(DecisionRequest, req.model_dump() | {
            "decision_id": str(uuid4()), "subjects": [subject.model_dump() | {"source_digest": digest("wrong")}]})
        with self.assertRaises(ContractError):
            await h.engine.evaluate(h.oid, wrong)
        self.assertEqual(provider.calls, 1)

    async def test_scope_boundaries_are_enforced_before_any_provider_call(self):
        policy = DecisionPolicy(family="jd-01", confidence_threshold=0.8)
        options = candidates("a", "b")
        provider = OfflineProvider()
        h = harness((policy,), lambda *_: options, provider)
        other_project = tuple(c.model_copy(update={"source": c.source.model_copy(update={"project_id": "other"})})
                              for c in options)
        h.engine.eligible_candidates = lambda *_: other_project
        self.assertEqual((await h.engine.evaluate(h.oid, request(h.oid, policy, other_project))).outcome.status,
                         "no_fit")
        h.engine.eligible_candidates = lambda *_: options
        secret_id, secret_sha = evidence(h, {"note": "sk" + "-" + "A" * 24})
        with self.assertRaises(ContractError):
            await h.engine.evaluate(h.oid, request(h.oid, policy, options, (
                SubjectBinding(name="note", artifact_ref=secret_id, source_digest=secret_sha),)))
        with self.assertRaises(ContractError):
            await h.engine.evaluate(str(uuid4()), request(h.oid, policy, options))
        h.accountant.close(h.oid)
        with self.assertRaises(ContractError):
            await h.engine.evaluate(h.oid, request(h.oid, policy, options))
        h.scope.close_scope(h.oid)
        with self.assertRaises(ContractError):
            await h.engine.evaluate(h.oid, request(h.oid, policy, options))
        self.assertEqual(provider.calls, 0)


class MeteredProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_metered_live_provider_admitted_with_hard_question_ceiling(self):
        policy = DecisionPolicy(family="jd-02", confidence_threshold=0.0)
        options = candidates("pdf", "none")
        inner = OfflineProvider()
        live = MeteredJevProvider(models=("jev-1.13.0",), max_questions=1, inner=inner)
        h = harness((policy,), lambda *_: options, live)
        first = await h.engine.evaluate(h.oid, request(h.oid, policy, options))
        self.assertEqual(first.source, "jev")
        self.assertEqual(live.questions, 1)
        self.assertEqual(live.tokens["input"], 20)
        wider = candidates("pdf", "none", "docx")
        h.engine.eligible_candidates = lambda *_: wider
        second = await h.engine.evaluate(h.oid, request(h.oid, policy, wider))
        # The runtime reports the refused dispatch as unresolved; nothing is accepted and the fallback stays.
        self.assertEqual((second.outcome.status, second.fallback_candidate), ("abstained", "none"))
        self.assertEqual(inner.calls, 1)
        refused = (TypeSafeProvider(api_key="x", default_model="jev-1.13.0"), type("Live", (), {"live": True})(),
                   MeteredJevProvider(max_questions=1, inner=inner, is_qualified=lambda: False))
        for provider in refused:
            with self.subTest(provider=type(provider).__name__), self.assertRaises(UnsupportedCapability):
                harness((policy,), lambda *_: options, provider)

    def test_metered_provider_reads_key_only_from_named_environment_variable(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(UnsupportedCapability):
            MeteredJevProvider(max_questions=1, api_key_env="SYNTHETIC_UNSET_KEY")
        with self.assertRaises(ContractError):
            MeteredJevProvider(models=(), max_questions=1, inner=OfflineProvider())


if __name__ == "__main__":
    unittest.main()
