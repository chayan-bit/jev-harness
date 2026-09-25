# jev-harness

**Attach bounded, auditable Jev decisions to any agent harness, without giving the model any new authority.**

[![CI](https://github.com/chayan-bit/jev-harness/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chayan-bit/jev-harness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/chayan-bit/jev-harness?sort=semver)](https://github.com/chayan-bit/jev-harness/releases)
[![License: MIT](https://img.shields.io/github/license/chayan-bit/jev-harness)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Built on Jev-Frame](https://img.shields.io/badge/built%20on-Jev--Frame-6f42c1)](https://github.com/chayan-bit/Jev-Frame)

<p align="center">
  <img src="docs/demos/advisory.gif" alt="Running the jev-harness advisory example offline: four Jev choices become one advisory-only recommendation and a Codex hook context line" width="760">
  <br>
  <sub><a href="docs/demos/advisory.mp4">MP4 version</a> · rendered from <a href="docs/demos/advisory.tape">docs/demos/advisory.tape</a></sub>
</p>

jev-harness packages the Jev elements of an agent harness so that any harness can attach them directly.
It gives you three things: bounded advisory for host choices, source-bound shadow judgments, and offline threshold calibration.
Jev is TypeSafe's decision model, reached here through [Jev-Frame](https://github.com/chayan-bit/Jev-Frame).
This is an independent open-source project and not an official TypeSafe product.
Everything except a live analysis runs without an API key or network access, including the tests and the examples.

## Why jev-harness

Coding-agent harnesses make many small operational choices: how hard a task is, how much reasoning to spend, which skill to load, when to compact the context, and how to classify a failure.
Jev answers exactly this kind of question with a probability distribution over the options you supply, so it can advise on those choices without writing free text.
The hard part is doing that safely: advice must never widen permissions, stale evidence must not be trusted, and a threshold must be measured before anyone relies on it.
jev-harness is that safety layer, with small typed ports so it fits your harness instead of replacing it.

## Features

- **Bounded advisory.** `analyze_snapshot` asks four independent Jev Choice questions about a small, validated `HostSnapshot` and recommends only options you supplied.
- **Codex hooks.** The `jev-harness` command speaks the Codex command-hook protocol, adds advice as context only, and can deny only through your own exact deny policy.
- **Shadow judgments.** `Decisions` evaluates seven decision families over digest-bound evidence and a trusted candidate catalog, and its advice never carries execution authority.
- **Calibration.** `corpus`, `freeze`, `held_out`, and `pilot_report` choose a threshold on validation groups, report once on held-out groups, and never activate a policy.
- **Ports, not a framework.** Three `typing.Protocol` ports plus a catalog callback, with in-memory reference adapters.
- **Offline by default.** A scripted `OfflineChoiceProvider` drives the tests, the examples, and every demo.

| Element | Input | Output | Authority |
| --- | --- | --- | --- |
| Advisory (`analyze_snapshot`) | A bounded `HostSnapshot` of explicit host options | An `AdviceReport` recommending one difficulty, reasoning effort, skill and compaction action | Advisory only |
| Codex hooks (`render_hook`, `jev-harness hook`) | A Codex hook payload, an optional exact deny policy and an optional report | Official Codex hook JSON | Can only deny, and only through your own exact deny policy; advice never allows anything |
| Shadow judgments (`Decisions`) | A `DecisionRequest` with digest-bound evidence and a trusted candidate catalog | `AdviceV2` shadow advice | `execution_authority` is always false |
| Calibration (`corpus`, `freeze`, `held_out`, `pilot_report`) | Opt-in captures and evaluator-only labels | A frozen threshold study and held-out report | `activation_allowed` and `promotion_allowed` are always false |

## Install

jev-harness requires Python 3.11 or newer and runs on Linux and macOS.
It is not published on PyPI yet, so install it from a tagged Git revision:

```sh
pip install "jev-harness @ git+https://github.com/chayan-bit/jev-harness@v0.1.1" # x-release-please-version
```

This also installs Jev-Frame from its public `v0.1.0` tag over HTTPS.
Importing `jev_harness` performs no network request and no credential lookup.

## Quickstart (60 seconds, no API key)

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

Running it prints:

```text
4
{'difficulty': 'routine', 'reasoning_effort': 'low', 'skill': 'none', 'compaction': 'defer', 'confidence': 0.9}
```

For a live analysis, replace the offline provider with `typesafe_provider(os.environ["TYPESAFE_API_KEY"])`.
[`examples/advisory.py`](examples/advisory.py) shows both paths and runs live only when you pass `--live`.

## Core concepts

| Concept | What it is |
| --- | --- |
| `HostSnapshot` | A validated, size-bounded description of the current task and the options your harness actually offers. Unknown fields are rejected. |
| `AdviceReport` | The advisory result: a recommendation or an `unresolved` status, a context digest, an expiry of at most five minutes, and advisory-only provenance. |
| `DenyPolicy` | Your exact deny lists (`denyToolNames`, `denyCommandSha256`); the only way a hook response can deny anything. |
| `Decisions` | The shadow-judgment engine: one registered `DecisionPolicy` per family, host ports, and a provider. |
| `DecisionRequest` / `AdviceV2` | A request that binds evidence by SHA-256 digest, and the resulting shadow advice with `execution_authority` fixed to false. |
| Ports | `DecisionScope`, `DecisionStore`, `JevAccountant`, and a `CandidateCatalog` callable that connect `Decisions` to your harness. |
| Providers | `OfflineChoiceProvider` for tests, and `MeteredJevProvider` around a live `typesafe_provider(...)` for real calls. |

## Usage guides

The snippets in this section run in CI, in order, through [`scripts/check_readme.py`](scripts/check_readme.py).

### Advisory

`analyze_snapshot` asks four independent Jev choice questions, one per dimension, and returns only options the caller supplied.
A completed analysis therefore makes four provider requests against the pinned `jev-1.13.0` model with one attempt each.
Low confidence (below 0.55 by default), a provider failure, a no-fit answer, empty option lists, unknown compaction state, unsupported host actions and stale snapshots all produce an `unresolved` report.
The last four are detected before any provider access.
A report expires after at most five minutes, and its context identifier appears only as a SHA-256 digest.
Guidance text is emitted only for the host actions you name, which must be among `model`, `skills`, `status` and `compact`.

### Codex hooks and exact deny policies

`render_hook` is the pure function behind the `jev-harness hook` command.
Advice can only add context, while an exact match in your deny policy is the only thing that denies:

```python
from jev_harness import DenyPolicy, render_hook

policy = DenyPolicy.from_mapping(
    {"schemaVersion": "1.0.0", "denyToolNames": ["apply_patch"], "denyCommandSha256": []}
)
payload = {
    "session_id": "session-1", "turn_id": "turn-1", "cwd": "/work", "model": "example-model",
    "hook_event_name": "PreToolUse", "permission_mode": "default",
    "tool_name": "apply_patch", "tool_input": {},
}
print(render_hook(payload, policy=policy)["hookSpecificOutput"]["permissionDecision"])
print(render_hook({**payload, "tool_name": "exec_command"}, policy=policy))
```

```text
deny
{}
```

The command speaks the Codex command-hook protocol for `PreToolUse`, `PermissionRequest`, `PreCompact` and `PostCompact`.
`PreToolUse` receives advice as `additionalContext`, the compaction events receive a non-controlling `systemMessage`, and `PermissionRequest` stays neutral.
A report is used only when it is fresh, matches the hook session by digest and has a lifetime of at most five minutes.
Permission output never contains an allow or approve decision.

### The command line

<p align="center"><img src="docs/demos/cli.gif" alt="jev-harness preview, hook-config, and hook commands running offline" width="760"></p>

`preview` compiles a snapshot offline, and `hook-config` prints a hooks configuration fragment without installing anything:

```sh
jev-harness preview < examples/snapshot.json | python -m json.tool | grep '"judgment_id"'
jev-harness hook-config --policy "$PWD/examples/policy.json" --report "$PWD/.local/current-advice.json"
```

Review the absolute paths in the fragment, then merge it through your normal Codex configuration workflow.
`jev-harness analyze --report .local/current-advice.json < examples/snapshot.json` runs one live analysis.
It reads the key from `TYPESAFE_API_KEY` (change it with `--api-key-env`) and appends each dispatch to a local ledger capped at 20 live requests.
Codex applies its own trust checks to command hooks, and this package never bypasses them.

### Shadow judgments

<p align="center"><img src="docs/demos/shadow-judgments.gif" alt="A source-bound shadow judgment classifying a test failure offline, with execution authority false" width="760"></p>

`Decisions` evaluates one of seven registered decision families:

| Family | Question |
| --- | --- |
| `jd-01` | Select an approved worker profile for the bounded task. |
| `jd-02` | Select an installed optional skill, or none, preserving explicit user requests. |
| `jd-03` | Classify a sanitized failure, preferring `unknown` over a guessed diagnosis. |
| `jd-04` | Assess whether one additional review would help, never removing a required final review. |
| `jd-05` | Select the supplied evidence most relevant to a bound question. |
| `jd-06` | Assess one narrow semantic criterion at a specific artifact revision, never overriding exact checks. |
| `jd-07` | Advise a context transition from known telemetry and validated checkpoint evidence. |

Each request binds evidence artifacts by SHA-256 digest, and candidates must come from your trusted catalog and from the scope's project.
Evidence larger than 16 KiB, evidence with private-key or API-token patterns, changed evidence, expired authority and closed budgets are refused before dispatch.
Zero candidates give `no_fit` and a single candidate is chosen deterministically, both without a provider call.
Otherwise one Jev question is asked, the inputs are revalidated before and after the call, and advice becomes `stale` if anything changed.
Accepted advice needs a confidence at or above the policy threshold, and an unset threshold never accepts.
`dispatch_candidate` returns what the host may act on: the deterministic single candidate or the policy fallback, never the Jev choice.
[`examples/shadow_judgments.py`](examples/shadow_judgments.py) wires all three in-memory adapters to an offline provider.

### Calibration

<p align="center"><img src="docs/demos/calibration.gif" alt="Calibrating a confidence threshold on a synthetic corpus: validation selection, held-out report, activation not allowed" width="760"></p>

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

### Generic Python

Any harness in Python can call `analyze_snapshot`, `render_hook` and `Decisions.evaluate` directly.
[`tests/test_attach.py`](tests/test_attach.py) shows a toy harness that implements all three ports on one class and uses only names exported from `jev_harness`.
For decisions that do not fit these elements, use [Jev-Frame](https://github.com/chayan-bit/Jev-Frame) directly; jev-harness is built on its public API.

## API overview

Everything public is exported from the top-level `jev_harness` package.

| Area | Main names | Purpose |
| --- | --- | --- |
| Advisory | `HostSnapshot`, `HostOption`, `analyze_snapshot`, `preview_snapshot`, `AdviceReport`, `AdvisoryRecommendation` | Bounded host-choice advice and offline previews. |
| Hooks | `render_hook`, `DenyPolicy`, `context_digest` | Codex hook responses that can only add context or apply your exact denies. |
| Shadow judgments | `Decisions`, `DecisionPolicy`, `DecisionRequest`, `DecisionCandidate`, `SubjectBinding`, `SourceRecord`, `AdviceV2`, `DecisionOutcome`, `FAMILIES` | Source-bound advice over trusted catalogs. |
| Ports and adapters | `DecisionScope`, `DecisionStore`, `JevAccountant`, `CandidateCatalog`, `ScopeGrant`, `StoredDecision`, `InMemoryScope`, `InMemoryDecisionStore`, `InMemoryAccountant` | The seams to your harness and their reference implementations. |
| Providers | `OfflineChoiceProvider`, `MeteredJevProvider`, `typesafe_provider` | Offline doubles and metered live access. |
| Calibration | `capture`, `corpus`, `replay`, `threshold_policy`, `freeze`, `held_out`, `wilson_upper`, `pilot_report`, `CapturedDecision`, `DecisionLabel`, `FrozenStudy`, `PilotRun` | Opt-in captures, grouped splits, frozen thresholds, and held-out reports. |
| Records and errors | `canonical_json`, `digest`, `parse_record`, `reject_secret_indicators`, `InputError`, `ContractError`, `PermissionDenied`, `BudgetExceeded`, `IdempotencyConflict`, `UnsupportedCapability` | Canonical serialization, validation, and typed failures. |

## Safety and authority boundaries

Advice carries no execution or permission authority.
Your harness decides what to do, applies its own approvals and sandbox, and may ignore every recommendation.

- Put only minimized, non-secret facts in a snapshot or an evidence artifact.
- Never include transcripts, prompts, file contents, environment variables, credentials, raw tool commands, home-directory paths or personal data.
- Snapshots are limited to 16 KiB with bounded fields, and unknown fields are rejected before any provider access.
- Context-window usage and account quota are separate measures, and quota is never accepted as a proxy for capacity.
- Captures are off unless you pass `enabled=True`, and real captures and labels should stay out of version control.
- A synthetic calibration pass never activates a policy; activation needs independent live evidence and human review outside this package.

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling keys.

## Limits

Recommendations are not calibrated reliability, and typed output does not guarantee correctness.
The analyzer does not discover host options, so missing options stay unresolved.
The hook does not refresh reports, so refresh advice when the task changes or the report expires.

## FAQ

**Do I need a TypeSafe API key?**
Only for a live analysis or live shadow judgments.
The tests, examples, previews, hooks, and calibration all run offline.

**Does it only work with Codex?**
No.
The hook command speaks the Codex protocol, but `analyze_snapshot`, `render_hook`, and `Decisions` are plain Python that any harness can call.

**Can advice approve a tool call?**
No.
Hook output never contains an allow or approve decision, and `execution_authority` is always false.

**How is this different from Jev-Frame?**
[Jev-Frame](https://github.com/chayan-bit/Jev-Frame) is the general framework for typed Jev decisions.
jev-harness is a small, opinionated set of harness-specific elements built on it.

**Is it on PyPI?**
Not yet; install from a tagged Git revision as shown above.

## Roadmap

Planned work and ideas are tracked in [GitHub issues](https://github.com/chayan-bit/jev-harness/issues) and [discussions](https://github.com/chayan-bit/jev-harness/discussions).
Release history is in [CHANGELOG.md](CHANGELOG.md), and the [documentation index](docs/README.md) lists everything else.

## Development

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests
uv run --frozen ruff check src tests examples scripts
uv run --frozen mypy src scripts
```

## Contributing

Contributions are welcome.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, commit conventions, and the demo-video requirement for pull requests, and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

jev-harness is released under the [MIT License](LICENSE).
