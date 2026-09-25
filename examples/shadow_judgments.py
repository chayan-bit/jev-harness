"""Attach source-bound Jev shadow judgments to an arbitrary harness through the ports.

Replace the three in-memory adapters with your own storage, evidence and budget
implementations of ``DecisionScope``, ``DecisionStore`` and ``JevAccountant``.
Runs offline with a scripted provider.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jev_harness import (
    DecisionCandidate,
    DecisionPolicy,
    DecisionRequest,
    Decisions,
    InMemoryAccountant,
    InMemoryDecisionStore,
    InMemoryScope,
    OfflineChoiceProvider,
    SourceRecord,
    SubjectBinding,
    digest,
)

PROJECT = "example-project"


def utc(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) + delta).isoformat().replace("+00:00", "Z")


def failure_classes() -> tuple[DecisionCandidate, ...]:
    labels = {"implementation": "The code under test is wrong.", "environment": "A tool or dependency is missing.",
              "unknown": "The evidence does not support a diagnosis."}
    return tuple(DecisionCandidate(candidate_id=key, description=text,
                                   source=SourceRecord(project_id=PROJECT, revision="catalog-v1",
                                                       content_digest=None, observed_at=utc()))
                 for key, text in labels.items())


async def main() -> None:
    scope_id = str(uuid4())  # one objective, run or session in your harness
    scope, store, accountant = InMemoryScope(), InMemoryDecisionStore(), InMemoryAccountant(max_questions=4)
    scope.open_scope(scope_id, project_id=PROJECT, ttl_seconds=600)

    # Evidence is bound by digest: if it changes before dispatch, the decision goes stale.
    failure = json.dumps({"test": "test_parser_roundtrip", "error": "AssertionError: 3 != 4"}).encode()
    artifact_id, sha256 = scope.add_evidence(scope_id, failure)

    policy = DecisionPolicy(family="jd-03", confidence_threshold=0.8)
    catalog = failure_classes()
    decisions = Decisions((policy,), lambda _scope, _family: catalog, scope=scope, store=store,
                          accountant=accountant, provider=OfflineChoiceProvider())
    request = DecisionRequest(
        decision_id=str(uuid4()), family=policy.family, version=policy.version,
        subjects=(SubjectBinding(name="failure", artifact_ref=artifact_id, source_digest=sha256),),
        evidence_refs=(), candidates=catalog, scope=scope_id, requested_model=policy.model,
        policy_artifact_digest=digest(policy), deadline_at=utc(timedelta(minutes=5)))

    advice = await decisions.evaluate(scope_id, request)
    print("shadow advice:", advice.outcome.status, advice.outcome.selected_candidate,
          f"confidence={advice.outcome.native_confidence}")
    print("execution authority:", advice.execution_authority)
    # What the harness may actually act on: never the Jev choice, only a deterministic or fallback candidate.
    print("host-safe candidate:", decisions.dispatch_candidate(scope_id, request))
    print("audit:", [event.kind for event in store.events], "questions spent:", accountant.spent[scope_id])
    assert advice.outcome.status == "accepted" and advice.execution_authority is False


if __name__ == "__main__":
    asyncio.run(main())
