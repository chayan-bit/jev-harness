from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from jev_harness import OfflineChoiceProvider
from jev_harness.agent import analyze_snapshot, build_definition, preview_snapshot
from jev_harness.cli import LedgeredProvider
from jev_harness.models import HostOption, HostSnapshot, InputError

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def snapshot(**updates: Any) -> HostSnapshot:
    def options(*values: str) -> list[HostOption]:
        return [HostOption(value, f"Use {value} for this bounded synthetic case.") for value in values]

    value = {
        "context_id": "session-1",
        "captured_at": "2026-09-22T12:00:00Z",
        "task_summary": "Repair one bounded parser regression and run its tests.",
        "phase": "implementation",
        "pending_action_count": 2,
        "recent_failure_count": 1,
        "context_tokens_used": 20_000,
        "context_token_capacity": 100_000,
        "checkpoint_ready": True,
        "available_difficulties": options("routine", "complex"),
        "available_reasoning_efforts": options("low", "medium", "high"),
        "available_skills": options("none", "test-runner"),
        "available_compaction_actions": options("defer", "compact_now"),
        "supported_host_actions": ["model", "skills", "status", "compact"],
    }
    value.update(updates)
    return HostSnapshot(**value)


FakeProvider = OfflineChoiceProvider


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def test_definition_uses_one_capability_package(self) -> None:
        definition = build_definition()

        self.assertEqual(len(definition.packages), 1)
        self.assertEqual(definition.packages[0].id, "jev-harness-host-options")
        self.assertEqual(len(definition.packages[0].judgments), 4)

    def test_preview_is_compiled_without_provider_access(self) -> None:
        compiled = preview_snapshot(snapshot())

        self.assertEqual(compiled["definition_id"], "jev-harness-advisor")
        self.assertEqual(len(compiled["questions"]), 4)
        self.assertTrue(all(item["supplied"] for item in compiled["candidates"]))
        self.assertIn("digest", compiled)

    async def test_runtime_returns_only_explicit_options_with_provenance_and_usage(self) -> None:
        provider = FakeProvider()

        report = await analyze_snapshot(snapshot(), provider, now=NOW)
        value = report.to_dict()

        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["recommendation"]["difficulty"], "routine")
        self.assertEqual(value["recommendation"]["reasoning_effort"], "low")
        self.assertEqual(value["recommendation"]["skill"], "none")
        self.assertEqual(value["recommendation"]["compaction"], "defer")
        self.assertEqual(value["usage"]["providerAttempts"], 4)
        self.assertEqual(value["usage"]["submittedQuestions"], 4)
        self.assertEqual(value["provenance"]["returnedModels"], ["jev-1.13.0"])
        self.assertIn("/model", value["guidance"]["model"])
        self.assertIn("task classification", value["guidance"]["model"])
        self.assertNotIn("reported model", value["guidance"]["model"])
        self.assertIn("/compact", value["guidance"]["compact"])
        self.assertEqual(provider.calls, 4)

    async def test_direct_construction_is_revalidated_before_provider_access(self) -> None:
        excessive_summary = snapshot(task_summary="x" * 100_000)
        excessive_option = snapshot()
        excessive_option.available_skills.append(
            HostOption("unsafe", "x" * 10_000)
        )

        for value in (excessive_summary, excessive_option):
            with self.subTest(task_summary_length=len(value.task_summary)):
                provider = FakeProvider()
                with self.assertRaises(InputError):
                    await analyze_snapshot(value, provider, now=NOW)
                self.assertEqual(provider.calls, 0)

    def test_preview_revalidates_mutated_direct_construction(self) -> None:
        value = snapshot()
        value.supported_host_actions.append("x" * 10_000)

        with self.assertRaises(InputError):
            preview_snapshot(value)

    async def test_missing_options_are_unresolved_without_provider_access(self) -> None:
        provider = FakeProvider()

        report = await analyze_snapshot(
            snapshot(available_skills=[]), provider, now=NOW
        )

        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.reasons, ["missing_options:available_skills"])
        self.assertEqual(provider.calls, 0)

    async def test_stale_context_is_unresolved_without_provider_access(self) -> None:
        provider = FakeProvider()

        report = await analyze_snapshot(
            snapshot(captured_at="2026-09-22T11:00:00Z"), provider, now=NOW
        )

        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.reasons, ["stale_context"])
        self.assertEqual(provider.calls, 0)

    async def test_unknown_compaction_state_is_unresolved_without_guessing(self) -> None:
        provider = FakeProvider()

        report = await analyze_snapshot(
            snapshot(context_tokens_used=None), provider, now=NOW
        )

        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.reasons, ["unknown_compaction_state"])
        self.assertEqual(provider.calls, 0)

    async def test_unsupported_host_action_is_unresolved(self) -> None:
        provider = FakeProvider()

        report = await analyze_snapshot(
            snapshot(supported_host_actions=["invented"]), provider, now=NOW
        )

        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.reasons, ["unsupported_host_action:invented"])
        self.assertEqual(provider.calls, 0)

    async def test_low_confidence_is_unresolved(self) -> None:
        report = await analyze_snapshot(
            snapshot(), FakeProvider(confidence=0.2), now=NOW
        )

        self.assertEqual(report.status, "unresolved")
        self.assertIsNone(report.recommendation)

    async def test_unsupported_no_fit_is_unresolved(self) -> None:
        report = await analyze_snapshot(snapshot(), FakeProvider(no_fit=True), now=NOW)

        self.assertEqual(report.status, "unresolved")
        self.assertIsNone(report.recommendation)

    async def test_provider_failure_becomes_unresolved_report(self) -> None:
        report = await analyze_snapshot(snapshot(), FakeProvider(fail=True), now=NOW)

        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.reasons, ["provider_or_runtime_failure"])

    async def test_live_ledger_records_dispatch_before_sanitized_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "attempts.jsonl"
            provider = LedgeredProvider(FakeProvider(), path)
            await provider.evaluate(
                questions=(),
                requested_model="jev-1.13.0",
                operation_id="synthetic-operation",
            )
            entries = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual([item["kind"] for item in entries], ["dispatch", "result"])
        self.assertEqual(entries[0]["ordinal"], entries[1]["ordinal"])
        self.assertEqual(entries[1]["status"], "succeeded")
        self.assertGreaterEqual(entries[1]["latencyMs"], 0)


if __name__ == "__main__":
    unittest.main()
