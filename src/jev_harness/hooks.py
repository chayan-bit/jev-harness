"""Render official Codex hook responses from a deny policy and an optional advisory report."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import (
    MAX_REPORT_TTL_SECONDS,
    MIN_ADVISORY_CONFIDENCE,
    InputError,
    context_digest,
    parse_timestamp,
    utc_now,
)

MAX_HOOK_BYTES = 65_536
SUPPORTED_EVENTS = {"PreToolUse", "PermissionRequest", "PreCompact", "PostCompact"}


@dataclass(frozen=True, slots=True)
class DenyPolicy:
    deny_tool_names: frozenset[str]
    deny_command_sha256: frozenset[str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DenyPolicy:
        if not isinstance(value, Mapping) or value.get("schemaVersion") != "1.0.0":
            raise InputError("policy requires schemaVersion 1.0.0")
        expected = {"schemaVersion", "denyToolNames", "denyCommandSha256"}
        if set(value) != expected:
            raise InputError("policy fields do not match schema")
        tools = value["denyToolNames"]
        digests = value["denyCommandSha256"]
        if (
            not isinstance(tools, list)
            or len(tools) > 128
            or any(type(item) is not str or not item or len(item) > 128 for item in tools)
        ):
            raise InputError("denyToolNames must contain bounded tool names")
        if (
            not isinstance(digests, list)
            or len(digests) > 128
            or any(
                type(item) is not str
                or len(item) != 64
                or any(char not in "0123456789abcdef" for char in item)
                for item in digests
            )
        ):
            raise InputError("denyCommandSha256 must contain lowercase SHA-256 digests")
        return cls(frozenset(tools), frozenset(digests))


def _validate_payload(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError("hook payload must be an object")
    if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()) > MAX_HOOK_BYTES:
        raise InputError("hook payload is too large")
    event = value.get("hook_event_name")
    if type(event) is not str or event not in SUPPORTED_EVENTS:
        raise InputError("unsupported hook event")
    for field in ("session_id", "turn_id", "cwd", "model"):
        if type(value.get(field)) is not str or not value[field] or len(value[field]) > 4_096:
            raise InputError(f"invalid {field}")
    if event in {"PreToolUse", "PermissionRequest"}:
        if type(value.get("tool_name")) is not str or not value["tool_name"]:
            raise InputError("invalid tool_name")
        if not isinstance(value.get("tool_input"), Mapping):
            raise InputError("invalid tool_input")
        if type(value.get("permission_mode")) is not str:
            raise InputError("invalid permission_mode")
    else:
        if type(value.get("trigger")) is not str or not value["trigger"]:
            raise InputError("invalid compaction trigger")
    return value


def _deny_output(event: str, reason: str) -> dict[str, Any]:
    if event == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    if event == "PermissionRequest":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": reason},
            }
        }
    return {}


def _policy_denial(payload: Mapping[str, Any], policy: DenyPolicy) -> str | None:
    if payload["hook_event_name"] not in {"PreToolUse", "PermissionRequest"}:
        return None
    if payload["tool_name"] in policy.deny_tool_names:
        return "Blocked by an exact configured tool-name policy."
    command = payload["tool_input"].get("command")
    if type(command) is str:
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
        if digest in policy.deny_command_sha256:
            return "Blocked by an exact configured command-digest policy."
    return None


def _advisory_text(
    report: Mapping[str, Any], payload: Mapping[str, Any], now: datetime
) -> str | None:
    if report.get("schemaVersion") != "1.0.0" or report.get("status") != "completed":
        return None
    if report.get("contextDigest") != context_digest(payload["session_id"]):
        return None
    try:
        generated = parse_timestamp(report["generatedAt"], "generatedAt")
        expires = parse_timestamp(report["expiresAt"], "expiresAt")
    except (InputError, KeyError, TypeError):
        return None
    if (
        generated > now
        or expires <= generated
        or (expires - generated).total_seconds() > MAX_REPORT_TTL_SECONDS
        or expires <= now
    ):
        return None
    recommendation = report.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return None
    fields: dict[str, str] = {}
    for field in ("difficulty", "reasoning_effort", "skill", "compaction"):
        value = recommendation.get(field)
        if type(value) is not str or not value or len(value) > 64:
            return None
        fields[field] = value
    confidence = recommendation.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not MIN_ADVISORY_CONFIDENCE <= confidence <= 1
    ):
        return None
    return (
        "Jev advisory from a fresh bounded snapshot: "
        f"difficulty={fields['difficulty']}; "
        f"reasoning_effort={fields['reasoning_effort']}; "
        f"skill={fields['skill']}; compaction={fields['compaction']}; "
        f"confidence={float(confidence):.2f}. "
        "This advice carries no permission or execution authority."
    )


def render_hook(
    payload: Any,
    *,
    policy: DenyPolicy | None = None,
    report: Mapping[str, Any] | None = None,
    policy_error: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Render one official Codex hook response without executing provider advice."""

    if not isinstance(payload, Mapping):
        return {}
    event = payload.get("hook_event_name")
    if type(event) is str and event in {"PreToolUse", "PermissionRequest"}:
        if policy_error:
            return _deny_output(event, "Blocked because the configured deny policy is invalid.")
        if policy is not None:
            tool_name = payload.get("tool_name")
            tool_input = payload.get("tool_input")
            if type(tool_name) is str and tool_name in policy.deny_tool_names:
                return _deny_output(event, "Blocked by an exact configured tool-name policy.")
            if type(tool_name) is not str or not isinstance(tool_input, Mapping):
                return _deny_output(event, "Blocked because the security hook payload is malformed.")
            command = tool_input.get("command")
            if (
                policy.deny_command_sha256
                and "command" in tool_input
                and type(command) is not str
            ):
                return _deny_output(event, "Blocked because the security hook payload is malformed.")
            denial = _policy_denial(payload, policy)
            if denial is not None:
                return _deny_output(event, denial)
            try:
                oversized = len(
                    json.dumps(
                        payload, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ) > MAX_HOOK_BYTES
            except (TypeError, ValueError):
                return _deny_output(event, "Blocked because the security hook payload is malformed.")
            if oversized:
                return _deny_output(event, "Blocked because the security hook payload is too large.")
    try:
        checked = _validate_payload(payload)
    except (InputError, TypeError, ValueError):
        return {}
    event = checked["hook_event_name"]
    if report is None:
        return {}
    text = _advisory_text(report, checked, utc_now() if now is None else now)
    if text is None:
        return {}
    if event == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": text,
            }
        }
    if event == "PermissionRequest":
        return {}
    return {"systemMessage": text}
