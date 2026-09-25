import dataclasses
import unittest
from tempfile import TemporaryDirectory
from uuid import uuid4

from jev_frame import (
    CaseSplit,
    FrozenPolicyMismatch,
    PolicyAction,
    PolicyObservation,
    ShadowEffectError,
    ShadowObservation,
    Usage,
    UsageCoverage,
    run_shadow_comparison,
)

from jev_harness import (
    CapturedDecision,
    ContractError,
    DecisionLabel,
    DecisionOutcome,
    DecisionPolicy,
    IdempotencyConflict,
    PermissionDenied,
    PilotRun,
    capture,
    corpus,
    digest,
    freeze,
    held_out,
    pilot_report,
    split_for_group,
    wilson_upper,
)
from jev_harness.evaluation import ARMS
from tests.test_decisions import candidates, request


def new_id():
    return str(uuid4())


def observations(count=12):
    records = []
    labels = []
    policy = DecisionPolicy(family="jd-01")
    for i in range(count):
        req = request(new_id(), policy, candidates("small", "large"))
        outcome = DecisionOutcome(
            decision_id=req.decision_id,
            status="accepted",
            selected_candidate="small",
            native_confidence=0.8,
            evidence_digest=digest(()),
            candidate_digest=digest(req.candidates),
            requested_model=req.requested_model,
            returned_model=req.requested_model,
            policy_version=1,
            expires_at=req.deadline_at,
        )
        record = CapturedDecision(
            case_id=f"case-{i}",
            dataset_version="synthetic-v1",
            source_lineage=f"fixture-{i}",
            group_id=f"group-{i}",
            family="jd-01",
            request=req,
            outcome=outcome,
            actual_action="small",
            actual_outcome_observed=True,
            input_tokens=10,
            output_tokens=2,
            provider_attempts=1,
            usage_coverage="complete",
            evidence_level="offline_fixture",
            seed=0,
        )
        records.append(record)
        labels.append(
            DecisionLabel(
                case_id=record.case_id,
                acceptable_candidates=("small",),
                evaluator_version="1.0.0",
                provenance_digest=digest("private-label-" + str(i)),
            )
        )
    return tuple(records), tuple(labels)


def manifest(records, labels):
    return corpus(records, labels, dataset_id="synthetic-routing", version="synthetic-v1", split_seed=7)[0]


def pilot(count=2, repeats=3):
    return tuple(
        PilotRun(
            task_id=f"task-{i}",
            group_id=f"group-{i}",
            family_id="fixture",
            arm=arm,
            repetition=n,
            seed=n,
            permission_digest=digest("permission"),
            resource_digest=digest("resource"),
            source_digest=digest(str(i)),
            verified_complete=arm != "supervisor_jev",
            harmful_actions=0,
            intervention_ids=(),
            elapsed_seconds=10.0,
            jev_seconds=2.0 if arm == "supervisor_jev" else 0.0,
            cost_micro_usd=100,
            jev_cost_micro_usd=20 if arm == "supervisor_jev" else 0,
            usage_coverage="complete",
            evidence_level="offline_fixture",
        )
        for i in range(count)
        for n in range(1, repeats + 1)
        for arm in ARMS
    )


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_opt_in_private_idempotent_and_strict(self):
        records, _ = observations(1)
        with TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionDenied):
                capture(tmp, records[0])
            path = capture(tmp, records[0], enabled=True)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(capture(tmp, records[0], enabled=True), path)
            with self.assertRaises(IdempotencyConflict):
                capture(tmp, records[0].model_copy(update={"seed": 1}), enabled=True)
            with self.assertRaises(ContractError):
                capture(tmp, records[0].model_copy(update={"input_tokens": None}), enabled=True)

    async def test_frozen_selection_held_out_and_tamper(self):
        records, labels = observations()
        study = await freeze(manifest(records, labels), records, labels, thresholds=(0.5, 0.9), split_seed=7)
        self.assertEqual(study.threshold, 0.5)
        result = await held_out(study, manifest(records, labels), records, labels)
        self.assertFalse(result["activation_allowed"])
        self.assertFalse(result["numerical_gate_passed"])
        self.assertFalse(result["all_observations_live"])
        changed = tuple(
            v.model_copy(update={"acceptable_candidates": ("large",), "harmful_candidates": ("small",)})
            if split_for_group(r.group_id, seed=7) == CaseSplit.HELD_OUT
            else v
            for r, v in zip(records, labels, strict=True)
        )
        other = await freeze(manifest(records, changed), records, changed, thresholds=(0.5, 0.9), split_seed=7)
        self.assertEqual(other.threshold, study.threshold)
        negative = await held_out(other, manifest(records, changed), records, changed)
        self.assertGreater(negative["wrong_accepted_groups"], 0)
        with self.assertRaises(ContractError):
            await held_out(study, manifest(records, changed), records, changed)
        with self.assertRaises(ContractError):
            await freeze(manifest(records, labels), records, labels, thresholds=(True,), split_seed=7)
        with self.assertRaises(FrozenPolicyMismatch):
            await held_out(dataclasses.replace(study, model="changed"), manifest(records, labels), records, labels)

    async def test_independent_groups_and_synthetic_pass_never_activate(self):
        records, labels = observations(250)
        study = await freeze(manifest(records, labels), records, labels, thresholds=(0.5,), split_seed=7)
        result = await held_out(study, manifest(records, labels), records, labels)
        self.assertTrue(result["numerical_gate_passed"])
        self.assertFalse(result["activation_allowed"])
        self.assertAlmostEqual(wilson_upper(0, 100), 0.03699349820698568)
        records = tuple(
            r.model_copy(update={"group_id": "one-source", "source_lineage": "same-source"}) for r in records
        )
        # Source variants retain one split and never count as independent samples.
        self.assertEqual(len({split_for_group(r.group_id, seed=7) for r in records}), 1)
        m = manifest(records, labels)
        self.assertEqual(len({c.task.group_id for c in m.cases}), 1)
        leaked = tuple(r.model_copy(update={"source_lineage": "same-source"}) for r in observations(250)[0])
        with self.assertRaises(ContractError):
            manifest(leaked, labels)  # one source split across many "independent" groups

    async def test_paired_pilot_cost_overhead_unknown_and_groups(self):
        rows = pilot()
        result = pilot_report(rows)
        self.assertFalse(result["planned_sample_satisfied"])
        self.assertFalse(result["observed_no_drop_over_five_points"])
        self.assertEqual(result["metrics"]["supervisor_jev"]["elapsed_seconds_mean_including_jev"], 12)
        self.assertIsNone(result["metrics"]["supervisor_jev"]["cost_micro_usd_per_verified_completion"])
        changed = tuple(r.model_copy(update={"verified_complete": True}) for r in rows)
        self.assertEqual(
            pilot_report(changed)["metrics"]["supervisor_jev"]["cost_micro_usd_per_verified_completion"], 120
        )
        changed = tuple(r.model_copy(update={"cost_micro_usd": None, "usage_coverage": "unknown"}) for r in changed)
        self.assertIsNone(pilot_report(changed)["metrics"]["codex_alone"]["cost_micro_usd_per_verified_completion"])
        for invalid in (
            rows[:-1],
            rows + (rows[0],),
            rows[:-1] + (rows[-1].model_copy(update={"resource_digest": digest("different")}),),
        ):
            with self.assertRaises(ContractError):
                pilot_report(invalid)
        repeated = tuple(r.model_copy(update={"group_id": "same-source"}) for r in pilot(30))
        self.assertEqual(pilot_report(repeated)["independent_groups"], 1)

    async def test_pilot_arms_are_configurable_and_non_jev_arms_cannot_carry_jev_usage(self):
        rename = {
            "codex_alone": "agent_alone",
            "claude_alone": "agent_alone_b",
            "supervisor_deterministic": "harness_plain",
            "supervisor_jev": "harness_jev",
        }
        rows = tuple(r.model_copy(update={"arm": rename[r.arm]}) for r in pilot())
        with self.assertRaises(ContractError):
            pilot_report(rows)
        result = pilot_report(
            rows, treatment="harness_jev", baselines=("agent_alone", "agent_alone_b"), controls=("harness_plain",)
        )
        self.assertEqual(set(result["metrics"]), set(rename.values()))
        self.assertEqual(result["metrics"]["harness_jev"]["elapsed_seconds_mean_including_jev"], 12)
        leaked = tuple(r.model_copy(update={"jev_questions": 1}) if r.arm == "codex_alone" else r for r in pilot())
        with self.assertRaises(ContractError):
            pilot_report(leaked)
        with self.assertRaises(ContractError):
            pilot_report(pilot(), baselines=())

    async def test_public_shadow_boundary_never_runs_tools_or_invents_alternate_outcome(self):
        observation = PolicyObservation(
            "case",
            "group",
            "variant",
            {"candidate": "small", "confidence": 0.8},
            (),
            Usage(UsageCoverage.COMPLETE, 1, 1, 1, 1),
            "model",
            "model",
        )
        shadow = ShadowObservation(
            "shadow", observation, PolicyAction.HANDOFF, actual_outcome="observed", actual_outcome_observed=True
        )

        async def operation(value, executor):
            with self.assertRaises(ShadowEffectError):
                await executor.dispatch(None, {})
            return PolicyAction.ACCEPT

        result = (await run_shadow_comparison((shadow,), operation))[0]
        self.assertFalse(result.counterfactual_outcome_known)
        self.assertIsNone(result.counterfactual_outcome)
