"""Bounded Jev advisory for any host, offline by default.

Run offline:            python examples/advisory.py
Run one live analysis:  TYPESAFE_API_KEY=... python examples/advisory.py --live

The report is advice only: it carries no execution or permission authority.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from jev_harness import (
    HostSnapshot,
    OfflineChoiceProvider,
    analyze_snapshot,
    preview_snapshot,
    render_hook,
    typesafe_provider,
)


def build_snapshot(now: datetime) -> HostSnapshot:
    # Only minimized, non-secret facts: no file contents, credentials, prompts or transcripts.
    return HostSnapshot.from_mapping({
        "context_id": "example-session-1",
        "captured_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "task_summary": "Repair one bounded parser regression and run its unit tests.",
        "phase": "implementation",
        "pending_action_count": 2,
        "recent_failure_count": 1,
        "context_tokens_used": 40_000,
        "context_token_capacity": 200_000,
        "checkpoint_ready": True,
        "available_difficulties": [{"id": "routine", "description": "A small, well-understood change."},
                                   {"id": "complex", "description": "A change that needs design work."}],
        "available_reasoning_efforts": [{"id": "low", "description": "Fast, shallow reasoning."},
                                        {"id": "high", "description": "Slow, thorough reasoning."}],
        "available_skills": [{"id": "none", "description": "No skill applies."},
                             {"id": "test-runner", "description": "Runs and triages unit tests."}],
        "available_compaction_actions": [{"id": "defer", "description": "Keep the current context."},
                                         {"id": "compact_now", "description": "Summarise the thread now."}],
        "supported_host_actions": ["model", "skills", "status", "compact"],
    })


async def main() -> None:
    now = datetime.now(UTC)
    snapshot = build_snapshot(now)
    preview = preview_snapshot(snapshot)
    print("offline preview questions:", len(preview["questions"]))

    live = "--live" in sys.argv
    if live:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise SystemExit("set TYPESAFE_API_KEY for a live analysis")
        provider = typesafe_provider(api_key)
    else:
        provider = OfflineChoiceProvider()
    try:
        report = await analyze_snapshot(snapshot, provider, now=now)
    finally:
        await provider.aclose()
    print(json.dumps(report.to_dict()["recommendation"], sort_keys=True))
    print("authority:", report.provenance["authority"])

    # A Codex PreToolUse hook payload for the same session receives the advice as context only.
    payload = {"session_id": "example-session-1", "turn_id": "turn-1", "cwd": "/work", "model": "example-model",
               "hook_event_name": "PreToolUse", "permission_mode": "default", "tool_name": "exec_command",
               "tool_input": {"command": "python -m unittest"}}
    response = render_hook(payload, report=report.to_dict(), now=now)
    print(response["hookSpecificOutput"]["additionalContext"] if response else "no advisory context")
    assert live or report.status == "completed"


if __name__ == "__main__":
    asyncio.run(main())
