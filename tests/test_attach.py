"""A third-party harness attaches using only ``import jev_harness`` names."""

import hashlib
import json
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jev_harness as jh


class LedgerHarness:
    """A toy host that is not built on the in-memory adapters: one object implements all three ports."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.artifacts: dict[str, bytes] = {}
        self.rows: dict[str, jh.StoredDecision] = {}
        self.cache: dict[str, jh.AdviceV2] = {}
        self.audit: list[tuple[str, str]] = []
        self.questions: list[str] = []

    def admit(self, scope: str) -> jh.ScopeGrant:
        return jh.ScopeGrant(self.project_id, datetime.now(UTC) + timedelta(minutes=5))

    def read_evidence(self, scope: str, artifact_id: str) -> bytes | None:
        return self.artifacts.get(artifact_id)

    def exact_checks(self, scope: str) -> list[dict[str, str]]:
        return []

    def required_exact_check_failed(self, scope: str) -> bool:
        return False

    def load(self, decision_id: str) -> jh.StoredDecision | None:
        return self.rows.get(decision_id)

    def begin(self, scope: str, request: jh.DecisionRequest, request_digest: str, placeholder: jh.AdviceV2) -> None:
        if request.decision_id in self.rows:
            raise jh.IdempotencyConflict("exists")
        self.rows[request.decision_id] = jh.StoredDecision(request_digest, placeholder)

    def finish(self, scope: str, advice: jh.AdviceV2, *, cache_key: str | None) -> None:
        stored = self.rows[advice.outcome.decision_id]
        self.rows[advice.outcome.decision_id] = jh.StoredDecision(stored.request_digest, advice)
        if cache_key:
            self.cache[cache_key] = advice
        self.audit.append((scope, advice.outcome.status))

    def cached(self, cache_key: str) -> jh.AdviceV2 | None:
        return self.cache.get(cache_key)

    def admission_open(self, scope: str) -> bool:
        return True

    def timeout_seconds(self, scope: str) -> float:
        return 5.0

    def reserve_question(self, scope: str, decision_id: str) -> str:
        if len(self.questions) >= 3:
            raise jh.BudgetExceeded("toy allowance exhausted")
        self.questions.append(decision_id)
        return str(uuid4())

    def settle_question(self, scope: str, call_id: str, usage: object) -> None:
        return None


def skill_candidates(*names: str) -> tuple[jh.DecisionCandidate, ...]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return tuple(jh.DecisionCandidate(candidate_id=name, description=f"Synthetic skill {name}",
                                      source=jh.SourceRecord(project_id="toy", revision="v1", content_digest=None,
                                                             observed_at=now))
                 for name in names)


class AttachTests(unittest.IsolatedAsyncioTestCase):
    def test_every_exported_name_resolves(self) -> None:
        missing = [name for name in jh.__all__ if not hasattr(jh, name)]
        self.assertEqual(missing, [])

    def test_custom_and_in_memory_adapters_satisfy_the_port_protocols(self) -> None:
        toy = LedgerHarness("toy")
        for port in (jh.DecisionScope, jh.DecisionStore, jh.JevAccountant):
            self.assertIsInstance(toy, port)
        self.assertIsInstance(jh.InMemoryScope(), jh.DecisionScope)
        self.assertIsInstance(jh.InMemoryDecisionStore(), jh.DecisionStore)
        self.assertIsInstance(jh.InMemoryAccountant(), jh.JevAccountant)

    async def test_custom_harness_gets_shadow_advice_with_bound_evidence(self) -> None:
        toy = LedgerHarness("toy")
        scope = str(uuid4())
        options = skill_candidates("none", "pdf-reader")
        policy = jh.DecisionPolicy(family="jd-02", confidence_threshold=0.8)
        raw = json.dumps({"task": "Summarise one synthetic PDF."}).encode()
        artifact_id = str(uuid4())
        toy.artifacts[artifact_id] = raw
        subject = jh.SubjectBinding(name="task", artifact_ref=artifact_id, source_digest=hashlib.sha256(raw).hexdigest())
        engine = jh.Decisions((policy,), lambda _scope, _family: options, scope=toy, store=toy, accountant=toy,
                              provider=jh.OfflineChoiceProvider())
        request = jh.DecisionRequest(
            decision_id=str(uuid4()), family="jd-02", version=1, subjects=(subject,), evidence_refs=(),
            candidates=options, scope=scope, requested_model=policy.model, policy_artifact_digest=jh.digest(policy),
            deadline_at=(datetime.now(UTC) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"))
        advice = await engine.evaluate(scope, request)
        self.assertEqual((advice.source, advice.outcome.status, advice.outcome.selected_candidate),
                         ("jev", "accepted", "none"))
        self.assertFalse(advice.execution_authority)
        self.assertEqual(engine.dispatch_candidate(scope, request), "none")
        self.assertEqual(toy.audit, [(scope, "accepted")])
        self.assertEqual(len(toy.questions), 1)

    async def test_advisory_report_flows_into_a_codex_hook_response(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

        def options(*ids: str) -> list[jh.HostOption]:
            return [jh.HostOption(i, f"Synthetic option {i}.") for i in ids]

        snapshot = jh.HostSnapshot(
            context_id="toy-session", captured_at="2026-01-01T12:00:00Z", task_summary="Fix one synthetic bug.",
            phase="implementation", pending_action_count=1, recent_failure_count=0, context_tokens_used=1000,
            context_token_capacity=100000, checkpoint_ready=False, available_difficulties=options("routine"),
            available_reasoning_efforts=options("low", "high"), available_skills=options("none"),
            available_compaction_actions=options("defer"), supported_host_actions=["status"])
        self.assertEqual(len(jh.preview_snapshot(snapshot)["questions"]), 4)
        report = await jh.analyze_snapshot(snapshot, jh.OfflineChoiceProvider(), now=now)
        self.assertEqual(report.status, "completed")
        payload = {"session_id": "toy-session", "turn_id": "t1", "cwd": "/work", "model": "synthetic",
                   "hook_event_name": "PreCompact", "trigger": "manual"}
        hook = jh.render_hook(payload, report=report.to_dict(), now=now)
        self.assertIn("no permission or execution authority", hook["systemMessage"])


if __name__ == "__main__":
    unittest.main()
