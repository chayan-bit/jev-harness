from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from jev_harness.hooks import DenyPolicy, render_hook
from jev_harness.models import context_digest

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def payload(event: str = "PreToolUse", **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "transcript_path": None,
        "cwd": "/tmp/project",
        "hook_event_name": event,
        "model": "synthetic-model",
    }
    if event in {"PreToolUse", "PermissionRequest"}:
        value.update(
            {
                "permission_mode": "default",
                "tool_name": "exec_command",
                "tool_input": {"command": "python -m unittest"},
            }
        )
        if event == "PreToolUse":
            value["tool_use_id"] = "tool-1"
    else:
        value["trigger"] = "auto"
    value.update(updates)
    return value


def report(*, expired: bool = False, context: str = "session-1") -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "status": "completed",
        "generatedAt": "2026-09-22T11:59:00Z",
        "expiresAt": "2026-09-22T11:59:59Z" if expired else "2026-09-22T12:04:00Z",
        "contextDigest": context_digest(context),
        "recommendation": {
            "difficulty": "complex",
            "reasoning_effort": "high",
            "skill": "test-runner",
            "compaction": "defer",
            "confidence": 0.81,
        },
        "provenance": {"authority": "advisory-only"},
        "usage": {"providerAttempts": 1},
        "reasons": [],
    }


def strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [item for child in value.values() for item in strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in strings(child)]
    return [value] if isinstance(value, str) else []


class HookTests(unittest.TestCase):
    def test_pre_tool_use_injects_advice_without_permission_decision(self) -> None:
        output = render_hook(payload(), report=report(), now=NOW)

        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertIn("reasoning_effort=high", specific["additionalContext"])
        self.assertNotIn("permissionDecision", specific)

    def test_permission_request_never_authorizes(self) -> None:
        output = render_hook(
            payload("PermissionRequest"), report=report(), now=NOW
        )

        lowered = {item.lower() for item in strings(output)}
        self.assertNotIn("allow", lowered)
        self.assertNotIn("approve", lowered)
        self.assertNotIn("permissiondecision", lowered)
        self.assertEqual(output, {})

    def test_exact_tool_policy_denies_before_advice(self) -> None:
        policy = DenyPolicy(frozenset({"exec_command"}), frozenset())

        output = render_hook(payload(), policy=policy, report=report(), now=NOW)

        self.assertEqual(
            output["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertNotIn("additionalContext", output["hookSpecificOutput"])

    def test_exact_policy_cannot_be_bypassed_by_missing_advisory_metadata(self) -> None:
        policy = DenyPolicy(frozenset({"test_blocked"}), frozenset())
        value = payload(tool_name="test_blocked")
        del value["model"]

        output = render_hook(value, policy=policy, report=report(), now=NOW)

        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_recognized_malformed_security_payload_fails_closed_with_policy(self) -> None:
        policy = DenyPolicy(frozenset(), frozenset())
        value = payload()
        del value["tool_input"]

        output = render_hook(value, policy=policy, now=NOW)

        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_malformed_present_command_fails_closed_for_digest_policy(self) -> None:
        policy = DenyPolicy(frozenset(), frozenset({"0" * 64}))

        output = render_hook(
            payload(tool_input={"command": 7}), policy=policy, now=NOW
        )

        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_exact_command_digest_denies_permission_request(self) -> None:
        command = "python -m unittest"
        policy = DenyPolicy(
            frozenset(),
            frozenset({hashlib.sha256(command.encode()).hexdigest()}),
        )

        output = render_hook(payload("PermissionRequest"), policy=policy, now=NOW)

        self.assertEqual(
            output["hookSpecificOutput"]["decision"]["behavior"], "deny"
        )

    def test_invalid_configured_policy_fails_closed_for_security_events(self) -> None:
        output = render_hook(payload(), policy_error=True, now=NOW)

        self.assertEqual(
            output["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_stale_or_mismatched_advice_is_ignored(self) -> None:
        self.assertEqual(render_hook(payload(), report=report(expired=True), now=NOW), {})
        self.assertEqual(
            render_hook(payload(), report=report(context="other"), now=NOW), {}
        )

    def test_report_generation_and_ttl_are_validated(self) -> None:
        future = report()
        future["generatedAt"] = "2099-01-01T00:00:00Z"
        future["expiresAt"] = "2099-01-01T00:05:00Z"
        invalid_order = report()
        invalid_order["generatedAt"] = "2026-09-22T12:03:00Z"
        invalid_order["expiresAt"] = "2026-09-22T12:02:00Z"
        excessive_ttl = report()
        excessive_ttl["generatedAt"] = "2026-09-22T11:55:00Z"
        excessive_ttl["expiresAt"] = "2026-09-22T12:05:00Z"

        for value in (future, invalid_order, excessive_ttl):
            with self.subTest(report=value):
                self.assertEqual(render_hook(payload(), report=value, now=NOW), {})

    def test_low_confidence_report_is_ignored(self) -> None:
        value = report()
        value["recommendation"]["confidence"] = 0.54

        self.assertEqual(render_hook(payload(), report=value, now=NOW), {})

    def test_compaction_events_emit_advisory_system_message(self) -> None:
        for event in ("PreCompact", "PostCompact"):
            with self.subTest(event=event):
                output = render_hook(payload(event), report=report(), now=NOW)
                self.assertIn("compaction=defer", output["systemMessage"])

    def test_malformed_payload_is_neutral(self) -> None:
        for value in (None, [], {}, {"hook_event_name": "PreToolUse"}):
            with self.subTest(value=value):
                self.assertEqual(render_hook(value), {})

    def test_cli_hook_stdin_stdout_end_to_end(self) -> None:
        policy = {
            "schemaVersion": "1.0.0",
            "denyToolNames": ["exec_command"],
            "denyCommandSha256": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-m", "jev_harness", "hook", "--policy", str(path)],
                input=json.dumps(payload()),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_cli_malformed_non_string_names_are_neutral(self) -> None:
        for value in (
            payload(hook_event_name=[]),
            payload(hook_event_name={}),
            payload(tool_name=[]),
            payload(tool_name={}),
        ):
            with self.subTest(value=value):
                completed = subprocess.run(
                    [sys.executable, "-m", "jev_harness", "hook"],
                    input=json.dumps(value),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {})

    def test_cli_oversized_security_input_respects_configured_policy(self) -> None:
        command = "x" * 70_000
        policy = {
            "schemaVersion": "1.0.0",
            "denyToolNames": ["blocked_tool"],
            "denyCommandSha256": [hashlib.sha256(command.encode()).hexdigest()],
        }
        denied = (
            payload(tool_name="blocked_tool", tool_input={"padding": command}),
            payload(tool_input={"command": command}),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            for value in denied:
                with self.subTest(value=value["tool_name"]):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "jev_harness",
                            "hook",
                            "--policy",
                            str(path),
                        ],
                        input=json.dumps(value),
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(
                        completed.stderr,
                        "jev-harness blocked malformed or oversized configured-policy input\n",
                    )

        completed = subprocess.run(
            [sys.executable, "-m", "jev_harness", "hook"],
            input=json.dumps(payload(tool_input={"command": command})),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {})

    def test_cli_malformed_stdin_blocks_when_policy_is_configured(self) -> None:
        policy = {
            "schemaVersion": "1.0.0",
            "denyToolNames": [],
            "denyCommandSha256": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            for malformed in ("{", "[]"):
                with self.subTest(malformed=malformed):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "jev_harness",
                            "hook",
                            "--policy",
                            str(path),
                        ],
                        input=malformed,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(
                        completed.stderr,
                        "jev-harness blocked malformed or oversized configured-policy input\n",
                    )

        completed = subprocess.run(
            [sys.executable, "-m", "jev_harness", "hook"],
            input="[]",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {})

    def test_cli_deeply_nested_stdin_blocks_when_policy_is_configured(self) -> None:
        policy = {
            "schemaVersion": "1.0.0",
            "denyToolNames": [],
            "denyCommandSha256": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jev_harness",
                    "hook",
                    "--policy",
                    str(path),
                ],
                input="[" * 10_000 + "0" + "]" * 10_000,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "jev-harness blocked malformed or oversized configured-policy input\n",
        )

    def test_cli_invalid_configured_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text("{", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jev_harness",
                    "hook",
                    "--policy",
                    str(path),
                ],
                input=json.dumps(payload()),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_cli_non_utf8_configured_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"\xff")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jev_harness",
                    "hook",
                    "--policy",
                    str(path),
                ],
                input=json.dumps(payload()),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_cli_prints_all_supported_hook_wiring_without_installing(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "jev_harness", "hook-config"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(
            set(value["hooks"]),
            {"PreToolUse", "PermissionRequest", "PreCompact", "PostCompact"},
        )
        self.assertIn("jev_harness hook", value["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertIn(str(Path(sys.executable).absolute()), value["hooks"]["PreToolUse"][0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
