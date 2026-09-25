# jev-harness

jev-harness packages the Jev elements of an agent harness so that any harness can attach them directly.
It gives you three things: bounded advisory for host choices, source-bound shadow judgments, and offline threshold calibration.
Jev is TypeSafe's decision model, reached here through [Jev-Frame](https://github.com/chayan-bit/Jev-Frame).
This is an independent open-source project and not an official TypeSafe product.
Everything except a live analysis runs without an API key or network access, including the tests and the examples.

## What each element does

| Element | Input | Output | Authority |
| --- | --- | --- | --- |
| Advisory (`analyze_snapshot`) | A bounded `HostSnapshot` of explicit host options | An `AdviceReport` recommending one difficulty, reasoning effort, skill and compaction action | Advisory only |
| Codex hooks (`render_hook`, `jev-harness hook`) | A Codex hook payload, an optional exact deny policy and an optional report | Official Codex hook JSON | Can only deny, and only through your own exact deny policy; advice never allows anything |
| Shadow judgments (`Decisions`) | A `DecisionRequest` with digest-bound evidence and a trusted candidate catalog | `AdviceV2` shadow advice | `execution_authority` is always false |
| Calibration (`corpus`, `freeze`, `held_out`, `pilot_report`) | Opt-in captures and evaluator-only labels | A frozen threshold study and held-out report | `activation_allowed` and `promotion_allowed` are always false |

Advice carries no execution or permission authority.
Your harness decides what to do, applies its own approvals and sandbox, and may ignore every recommendation.

## Install

jev-harness requires Python 3.11 or newer and runs on Linux and macOS.
It is not published on PyPI yet, so install it from a tagged Git revision:

```sh
pip install "jev-harness @ git+https://github.com/chayan-bit/jev-harness@v0.1.0"
```

This also installs Jev-Frame from its public `v0.1.0` tag over HTTPS.
Importing `jev_harness` performs no network request and no credential lookup.

## Quickstart

This builds a snapshot, previews the compiled Jev questions, and runs the analysis with the bundled offline provider:

```python
import asyncio
from datetime import UTC, datetime

from jev_harness import HostSnapshot, OfflineChoiceProvider, analyze_snapshot, preview_snapshot


def opts(*ids):
    return [{"id": i, "description": f"Host option {i}."} for i in ids]


async def main():
    now = datetime.now(UTC)
    snapshot = HostSnapshot.from_mapping({
        "context_id": "session-1",
        "captured_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "task_summary": "Repair one bounded parser regression and run its tests.",
        "phase": "implementation",
        "pending_action_count": 2,
        "recent_failure_count": 1,
        "context_tokens_used": 20000,
        "context_token_capacity": 100000,
        "checkpoint_ready": True,
        "available_difficulties": opts("routine", "complex"),
        "available_reasoning_efforts": opts("low", "high"),
        "available_skills": opts("none", "test-runner"),
        "available_compaction_actions": opts("defer", "compact_now"),
        "supported_host_actions": ["model", "status"],
    })
    print(len(preview_snapshot(snapshot)["questions"]))  # 4, compiled without any provider
    report = await analyze_snapshot(snapshot, OfflineChoiceProvider(), now=now)
    print(report.to_dict()["recommendation"])


asyncio.run(main())
```

For a live analysis, replace the offline provider with `typesafe_provider(os.environ["TYPESAFE_API_KEY"])`.
[`examples/advisory.py`](examples/advisory.py) shows both paths and runs live only when you pass `--live`.

## The elements in detail

### Advisory

`analyze_snapshot` asks four independent Jev choice questions, one per dimension, and returns only options the caller supplied.
A completed analysis therefore makes four provider requests against the pinned `jev-1.13.0` model with one attempt each.
Low confidence (below 0.55 by default), a provider failure, a no-fit answer, empty option lists, unknown compaction state, unsupported host actions and stale snapshots all produce an `unresolved` report.
The last four are detected before any provider access.
A report expires after at most five minutes, and its context identifier appears only as a SHA-256 digest.
Guidance text is emitted only for the host actions you name, which must be among `model`, `skills`, `status` and `compact`.

### Codex hooks

The `jev-harness` command speaks the Codex command-hook protocol for `PreToolUse`, `PermissionRequest`, `PreCompact` and `PostCompact`.
`PreToolUse` receives advice as `additionalContext`, the compaction events receive a non-controlling `systemMessage`, and `PermissionRequest` stays neutral.
A report is used only when it is fresh, matches the hook session by digest and has a lifetime of at most five minutes.
The optional deny policy has two exact match sets, `denyToolNames` and `denyCommandSha256`, and an exact match emits the official deny shape.
Permission output never contains an allow or approve decision.

### Shadow judgments

`Decisions` evaluates one of seven registered decision families (`jd-01` to `jd-07`), such as worker-profile selection, optional-skill selection, failure classification and context-transition advice.
Each request binds evidence artifacts by SHA-256 digest, and candidates must come from your trusted catalog and from the scope's project.
Evidence larger than 16 KiB, evidence with private-key or API-token patterns, changed evidence, expired authority and closed budgets are refused before dispatch.
Zero candidates give `no_fit` and a single candidate is chosen deterministically, both without a provider call.
Otherwise one Jev question is asked, the inputs are revalidated before and after the call, and advice becomes `stale` if anything changed.
Accepted advice needs a confidence at or above the policy threshold, and an unset threshold never accepts.
`dispatch_candidate` returns what the host may act on: the deterministic single candidate or the policy fallback, never the Jev choice.

### Calibration

`capture` writes opt-in, write-once, private (0600) JSON captures.
`corpus` builds a Jev-Frame evaluation manifest and splits cases by independent group, so variants of one source never straddle the calibration and held-out sets.
`freeze` selects one of your predeclared thresholds on validation groups, preferring zero harmful acceptances before coverage.
`held_out` evaluates the frozen study once and reports coverage, wrong acceptances and a Wilson 95% upper bound (`wilson_upper`).
`pilot_report` summarises a paired comparison of a Jev arm against baseline and control arms, bootstrapping over independent groups.
[`examples/calibration.py`](examples/calibration.py) runs the whole flow on a synthetic corpus.

## Attach to your harness

### Ports

`Decisions` touches host state only through three `typing.Protocol` ports and one callback, all in `jev_harness.ports`.
Each port has an in-memory reference implementation in `jev_harness.memory`.

| Port | Methods | Reference adapter |
| --- | --- | --- |
| `DecisionScope` | `admit(scope) -> ScopeGrant`, `read_evidence(scope, artifact_id) -> bytes \| None`, `exact_checks(scope)`, `required_exact_check_failed(scope)` | `InMemoryScope` |
| `DecisionStore` | `load(decision_id)`, `begin(scope, request, request_digest, placeholder)`, `finish(scope, advice, cache_key=...)`, `cached(cache_key)` | `InMemoryDecisionStore` |
| `JevAccountant` | `admission_open(scope)`, `timeout_seconds(scope)`, `reserve_question(scope, decision_id) -> call_id`, `settle_question(scope, call_id, usage)` | `InMemoryAccountant` |
| `CandidateCatalog` | `(scope, family) -> Iterable[DecisionCandidate]` | Any callable |

A database-backed adapter should make each method one transaction, make `begin` fail on a duplicate decision id, and make `finish` update the advice, the cache and the audit trail atomically.
[`examples/shadow_judgments.py`](examples/shadow_judgments.py) wires all three in-memory adapters to an offline provider.

### Providers

`Decisions` refuses a raw `TypeSafeProvider`.
Pass `OfflineChoiceProvider` for tests, or wrap the live provider in `MeteredJevProvider`, which enforces a model catalog, one question per dispatch and a hard question ceiling.
Its `is_qualified` callback lets your harness revoke live access, for example when an operator approval expires.

### Codex hook integration

Print a hooks configuration fragment without installing anything, review its absolute paths, then merge it through your normal Codex configuration workflow:

```sh
jev-harness analyze --report .local/current-advice.json < examples/snapshot.json
jev-harness hook-config --policy "$PWD/examples/policy.json" --report "$PWD/.local/current-advice.json"
```

`analyze` reads the key from `TYPESAFE_API_KEY` (change it with `--api-key-env`) and appends each dispatch to a local ledger capped at 20 live requests.
`jev-harness preview < examples/snapshot.json` compiles the questions offline.
Codex applies its own trust checks to command hooks, and this package never bypasses them.

### Generic Python

Any harness in Python can call `analyze_snapshot`, `render_hook` and `Decisions.evaluate` directly.
[`tests/test_attach.py`](tests/test_attach.py) shows a toy harness that implements all three ports on one class and uses only names exported from `jev_harness`.

## Safety and privacy

Put only minimized, non-secret facts in a snapshot or an evidence artifact.
Never include transcripts, prompts, file contents, environment variables, credentials, raw tool commands, home-directory paths or personal data.
Snapshots are limited to 16 KiB with bounded fields, and unknown fields are rejected before any provider access.
Context-window usage and account quota are separate measures, and quota is never accepted as a proxy for capacity.
Captures are off unless you pass `enabled=True`, and real captures and labels should stay out of version control.
See [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling keys.

## Limits

Recommendations are not calibrated reliability, and typed output does not guarantee correctness.
The analyzer does not discover host options, so missing options stay unresolved.
The hook does not refresh reports, so refresh advice when the task changes or the report expires.
A synthetic calibration pass never activates a policy; activation needs independent live evidence and human review outside this package.

## Development

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ruff check src tests examples
uv run --frozen mypy src
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## License

jev-harness is released under the [MIT License](LICENSE).
