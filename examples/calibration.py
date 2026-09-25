"""Calibrate a confidence threshold on a synthetic captured corpus, entirely offline.

The threshold is chosen on validation groups only and then evaluated once on
held-out groups.  The report never allows activation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jev_harness import (
    CapturedDecision,
    DecisionCandidate,
    DecisionLabel,
    DecisionOutcome,
    DecisionPolicy,
    DecisionRequest,
    SourceRecord,
    corpus,
    digest,
    freeze,
    held_out,
)

POLICY = DecisionPolicy(family="jd-01")


def case(index: int) -> tuple[CapturedDecision, DecisionLabel]:
    now = datetime.now(UTC)
    options = tuple(DecisionCandidate(candidate_id=name, description=f"Synthetic worker profile {name}",
                                      source=SourceRecord(project_id="synthetic", revision="v1", content_digest=None,
                                                          observed_at=now.isoformat().replace("+00:00", "Z")))
                    for name in ("small", "large"))
    request = DecisionRequest(
        decision_id=str(uuid4()), family=POLICY.family, version=1, subjects=(), evidence_refs=(), candidates=options,
        scope=str(uuid4()), requested_model=POLICY.model, policy_artifact_digest=digest(POLICY),
        deadline_at=(now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"))
    # Synthetic pattern: confident answers are right, hesitant answers pick the wrong profile.
    confident = index % 4 != 0
    selected = "small" if confident else "large"
    outcome = DecisionOutcome(
        decision_id=request.decision_id, status="accepted", selected_candidate=selected,
        native_confidence=0.92 if confident else 0.6, evidence_digest=digest(()),
        candidate_digest=digest(request.candidates), requested_model=POLICY.model, returned_model=POLICY.model,
        policy_version=1, expires_at=request.deadline_at)
    captured = CapturedDecision(
        case_id=f"case-{index}", dataset_version="synthetic-v1", source_lineage=f"source-{index}",
        group_id=f"group-{index}", family=POLICY.family, request=request, outcome=outcome, actual_action=None,
        actual_outcome_observed=False, input_tokens=40, output_tokens=4, provider_attempts=1,
        usage_coverage="complete", evidence_level="offline_fixture", seed=0)
    label = DecisionLabel(case_id=captured.case_id, acceptable_candidates=("small",), harmful_candidates=("large",),
                          evaluator_version="1", provenance_digest=digest(f"synthetic-label-{index}"))
    return captured, label


async def main() -> None:
    pairs = [case(i) for i in range(80)]
    captures = tuple(p[0] for p in pairs)
    labels = tuple(p[1] for p in pairs)
    manifest, _, _ = corpus(captures, labels, dataset_id="synthetic-routing", version="synthetic-v1", split_seed=7)
    study = await freeze(manifest, captures, labels, thresholds=(0.5, 0.7, 0.9), split_seed=7)
    report = await held_out(study, manifest, captures, labels)
    print("selected threshold:", study.threshold)
    for key in ("held_out_groups", "accepted_independent_groups", "wrong_accepted_groups", "coverage",
                "wilson_95_upper_wrong", "numerical_gate_passed", "activation_allowed"):
        print(f"{key}: {report[key]}")
    assert study.threshold == 0.7 and report["wrong_accepted_groups"] == 0 and report["activation_allowed"] is False


if __name__ == "__main__":
    asyncio.run(main())
