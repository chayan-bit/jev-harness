"""The ``jev-harness`` command: Codex hook rendering, offline preview and one live analysis."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shlex
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent import analyze_snapshot, preview_snapshot, typesafe_provider
from .hooks import MAX_HOOK_BYTES, DenyPolicy, render_hook
from .models import MAX_REPORT_TTL_SECONDS, HostSnapshot, InputError

MODEL = "jev-1.13.0"
MAX_LIVE_REQUESTS = 20


def _read_json(limit: int) -> Any:
    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        raise InputError(f"stdin exceeds {limit} bytes")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise InputError("stdin must contain one JSON value") from error


def _load_json(path: Path) -> Any:
    if path.stat().st_size > MAX_HOOK_BYTES:
        raise InputError("configured JSON file is too large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise InputError("configured file must contain one JSON value") from error


def _emit(value: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n")


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LedgeredProvider:
    def __init__(self, provider: Any, ledger: Path) -> None:
        self.provider = provider
        self.ledger = ledger

    def _record(self, kwargs: Mapping[str, Any]) -> int:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            entries = [json.loads(line) for line in handle if line.strip()]
            count = sum(
                1 for item in entries if item.get("kind", "dispatch") == "dispatch"
            )
            if count >= MAX_LIVE_REQUESTS:
                raise RuntimeError("live request budget is exhausted")
            entry = {
                "kind": "dispatch",
                "ordinal": count + 1,
                "dispatchedAt": datetime.now(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "model": kwargs.get("requested_model"),
                "operationId": kwargs.get("operation_id"),
                "questionCount": len(kwargs.get("questions", ())),
                "status": "dispatching",
            }
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle, fcntl.LOCK_UN)
        return count + 1

    def _record_result(
        self,
        ordinal: int,
        *,
        status: str,
        latency_ms: float,
        returned_model: str | None = None,
        error_type: str | None = None,
    ) -> None:
        entry = {
            "kind": "result",
            "ordinal": ordinal,
            "finishedAt": datetime.now(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "status": status,
            "latencyMs": round(latency_ms, 3),
            "returnedModel": returned_model,
            "errorType": error_type,
        }
        with self.ledger.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle, fcntl.LOCK_UN)

    async def evaluate(self, **kwargs: Any) -> Any:
        ordinal = self._record(kwargs)
        started = time.perf_counter_ns()
        try:
            result = await self.provider.evaluate(**kwargs)
        except BaseException as error:
            self._record_result(
                ordinal,
                status="failed",
                latency_ms=(time.perf_counter_ns() - started) / 1e6,
                error_type=type(error).__name__,
            )
            raise
        self._record_result(
            ordinal,
            status="succeeded",
            latency_ms=(time.perf_counter_ns() - started) / 1e6,
            returned_model=result.returned_model,
        )
        return result

    async def aclose(self) -> None:
        await self.provider.aclose()


def _hook(args: argparse.Namespace) -> int:
    policy = None
    policy_error = False
    if args.policy is not None:
        try:
            policy = DenyPolicy.from_mapping(_load_json(args.policy))
        except (InputError, OSError, json.JSONDecodeError):
            policy_error = True
    try:
        payload = _read_json(MAX_HOOK_BYTES)
    except InputError:
        if args.policy is not None:
            sys.stderr.write(
                "jev-harness blocked malformed or oversized configured-policy input\n"
            )
            return 2
        _emit({})
        return 0
    if args.policy is not None and not isinstance(payload, Mapping):
        sys.stderr.write(
            "jev-harness blocked malformed or oversized configured-policy input\n"
        )
        return 2
    report = None
    if args.report is not None:
        try:
            value = _load_json(args.report)
            report = value if isinstance(value, Mapping) else None
        except (InputError, OSError, json.JSONDecodeError):
            report = None
    _emit(
        render_hook(
            payload,
            policy=policy,
            report=report,
            policy_error=policy_error,
        )
    )
    return 0


def _preview(_: argparse.Namespace) -> int:
    try:
        snapshot = HostSnapshot.from_mapping(_read_json(16_384))
        _emit(preview_snapshot(snapshot))
    except (InputError, TypeError, ValueError) as error:
        _emit({"status": "error", "reasons": [type(error).__name__]})
        return 2
    return 0


def _hook_config(args: argparse.Namespace) -> int:
    command = [str(Path(sys.executable).absolute()), "-m", "jev_harness", "hook"]
    if args.policy is not None:
        command.extend(("--policy", str(args.policy.resolve())))
    if args.report is not None:
        command.extend(("--report", str(args.report.resolve())))
    rendered = " ".join(shlex.quote(item) for item in command)
    handler = {
        "type": "command",
        "command": rendered,
        "timeout": 2,
        "statusMessage": "Checking bounded Jev harness policy",
    }
    _emit(
        {
            "description": "jev-harness local policy and advisory integration",
            "hooks": {
                "PreToolUse": [{"matcher": "*", "hooks": [handler]}],
                "PermissionRequest": [{"matcher": "*", "hooks": [handler]}],
                "PreCompact": [{"hooks": [handler]}],
                "PostCompact": [{"hooks": [handler]}],
            },
        }
    )
    return 0


async def _analyze_async(args: argparse.Namespace) -> int:
    try:
        snapshot = HostSnapshot.from_mapping(_read_json(16_384))
    except (InputError, TypeError, ValueError) as error:
        _emit({"schemaVersion": "1.0.0", "status": "error", "reasons": [type(error).__name__]})
        return 2
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        _emit(
            {
                "schemaVersion": "1.0.0",
                "status": "error",
                "reasons": ["provider_key_unavailable"],
            }
        )
        return 2
    provider = LedgeredProvider(typesafe_provider(api_key, model=MODEL), args.ledger)
    try:
        report = await analyze_snapshot(
            snapshot,
            provider,
            model=MODEL,
            ttl_seconds=args.ttl,
            timeout_seconds=args.timeout,
        )
        value = report.to_dict()
    except Exception:
        value = {
            "schemaVersion": "1.0.0",
            "status": "error",
            "reasons": ["provider_or_runtime_failure"],
        }
    finally:
        await provider.aclose()
    if args.report is not None:
        _write_private_json(args.report, value)
    _emit(value)
    return 0 if value.get("status") in {"completed", "unresolved"} else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev-harness")
    commands = parser.add_subparsers(dest="command", required=True)
    hook = commands.add_parser("hook", help="Handle one Codex hook payload from stdin.")
    hook.add_argument("--policy", type=Path)
    hook.add_argument("--report", type=Path)
    hook.set_defaults(handler=_hook)
    preview = commands.add_parser("preview", help="Compile a snapshot without provider access.")
    preview.set_defaults(handler=_preview)
    config = commands.add_parser(
        "hook-config", help="Print a Codex hooks.json fragment without installing it."
    )
    config.add_argument("--policy", type=Path)
    config.add_argument("--report", type=Path)
    config.set_defaults(handler=_hook_config)
    analyze = commands.add_parser("analyze", help="Run one bounded Jev advisory analysis.")
    analyze.add_argument("--api-key-env", default="TYPESAFE_API_KEY")
    analyze.add_argument("--ledger", type=Path, default=Path(".local/live/attempts.jsonl"))
    analyze.add_argument("--report", type=Path)
    analyze.add_argument("--ttl", type=int, default=MAX_REPORT_TTL_SECONDS)
    analyze.add_argument("--timeout", type=float, default=8.0)
    analyze.set_defaults(handler=lambda args: asyncio.run(_analyze_async(args)))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
