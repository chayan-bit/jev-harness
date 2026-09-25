"""Bounded Jev advisory for any host: HostSnapshot in, advisory-only AdviceReport out."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from jev_frame import (
    AgentDefinition,
    Candidate,
    CandidateBinding,
    CandidateProvider,
    CandidateSet,
    CapabilityPackage,
    ChoiceAnswer,
    ChoiceQuestion,
    CompletionContract,
    ConstantBinding,
    Coverage,
    HostContextBinding,
    Judgment,
    JudgmentBinding,
    OperatingPolicy,
    ProviderBatch,
    RunContext,
    RunLimits,
    Runtime,
    Subject,
    TaskInputBinding,
    TerminalStatus,
    Tool,
    TypeSafeProvider,
    preview_agent,
)

from .models import (
    MAX_REPORT_TTL_SECONDS,
    MIN_ADVISORY_CONFIDENCE,
    AdviceReport,
    AdvisoryRecommendation,
    HostOption,
    HostSnapshot,
    context_digest,
    format_timestamp,
    utc_now,
)

DEFINITION_ID = "jev-harness-advisor"
DEFINITION_VERSION = "1.0.0"
PACKAGE_ID = "jev-harness-host-options"
PACKAGE_VERSION = "1.0.0"
FRAMEWORK_REVISION = "20798cbb26f7665bd31d5fb639fb910eb123d154"  # Jev-Frame v0.1.0
GUIDANCE = {
    "model": "Treat the reported difficulty as a task classification. Use /model only to choose the reported reasoning effort, and select only an option the picker offers.",
    "skills": "Use /skills to inspect installed skills, and invoke the reported skill only when its description matches the task.",
    "status": "Use /status to inspect session configuration and context token usage; /usage account limits are a separate measure.",
    "compact": "Run /compact only after checkpoint readiness is true and context-window usage justifies summarizing the thread.",
}
DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    (
        "difficulty",
        "available_difficulties",
        "Choose the task difficulty that best fits the supplied bounded host snapshot.",
    ),
    (
        "reasoning_effort",
        "available_reasoning_efforts",
        "Choose the reasoning effort that best fits the supplied bounded host snapshot.",
    ),
    (
        "skill",
        "available_skills",
        "Choose the single available skill that is most useful for the current task.",
    ),
    (
        "compaction",
        "available_compaction_actions",
        "Choose the available compaction action that best preserves task continuity.",
    ),
)


def _option_snapshot(options: list[HostOption], dimension: str, scope: str) -> CandidateSet:
    return CandidateSet(
        dimension,
        "1.0.0",
        tuple(
            Candidate(
                key=f"option-{index}",
                value=option.id,
                description=option.description,
                source_id="caller:explicit-host-options",
                source_version="1",
            )
            for index, option in enumerate(options)
        ),
        Coverage.COMPLETE,
        scope,
        total_count=len(options),
        retrieval_parameters={"source": "explicit-caller-payload"},
    )


def _assemble_recommendation(
    difficulty: str,
    reasoning_effort: str,
    skill: str,
    compaction: str,
    difficulty_answer: ChoiceAnswer,
    reasoning_effort_answer: ChoiceAnswer,
    skill_answer: ChoiceAnswer,
    compaction_answer: ChoiceAnswer,
) -> AdvisoryRecommendation:
    return AdvisoryRecommendation(
        difficulty=difficulty,
        reasoning_effort=reasoning_effort,
        skill=skill,
        compaction=compaction,
        confidence=min(
            difficulty_answer.confidence,
            reasoning_effort_answer.confidence,
            skill_answer.confidence,
            compaction_answer.confidence,
        ),
    )


def build_capability_package() -> CapabilityPackage:
    judgments = tuple(
        Judgment(
            id=dimension,
            version="1.0.0",
            primitive=ChoiceQuestion(instructions),
            subjects=tuple(
                Subject(name)
                for name in (
                    "task_summary",
                    "phase",
                    "pending_action_count",
                    "recent_failure_count",
                    *(
                        (
                            "context_tokens_used",
                            "context_token_capacity",
                            "checkpoint_ready",
                        )
                        if dimension == "compaction"
                        else ()
                    ),
                )
            ),
            candidate_set=f"{dimension}_options",
        )
        for dimension, _, instructions in DIMENSIONS
    )
    providers = tuple(
        CandidateProvider(
            id=f"{dimension}_options",
            version="1.0.0",
            function=_option_snapshot,
            bindings={
                "options": TaskInputBinding((input_field,)),
                "dimension": ConstantBinding(f"{dimension}_options"),
                "scope": HostContextBinding("scope"),
            },
        )
        for dimension, input_field, _ in DIMENSIONS
    )
    bindings: dict[str, Any] = {
        dimension: CandidateBinding(f"{dimension}_options", dimension)
        for dimension, _, _ in DIMENSIONS
    }
    bindings.update(
        {
            f"{dimension}_answer": JudgmentBinding(dimension)
            for dimension, _, _ in DIMENSIONS
        }
    )
    assemble = Tool(
        id="assemble_recommendation",
        version="1.0.0",
        purpose="Assemble selected explicit host options without side effects.",
        function=_assemble_recommendation,
        bindings=bindings,
        produces_evidence=("recommendation",),
    )
    return CapabilityPackage(
        PACKAGE_ID,
        PACKAGE_VERSION,
        tools=(assemble,),
        judgments=judgments,
        candidate_providers=providers,
    )


def build_definition() -> AgentDefinition[HostSnapshot, AdvisoryRecommendation]:
    return AgentDefinition(
        id=DEFINITION_ID,
        version=DEFINITION_VERSION,
        objective=(
            "Recommend only explicitly available host options from a bounded snapshot; "
            "the result is advisory and carries no execution or permission authority."
        ),
        input_type=HostSnapshot,
        output_type=AdvisoryRecommendation,
        completion=CompletionContract(
            ("recommendation",), {}, "advisory-completion"
        ),
        policy=OperatingPolicy("1.0.0", ("advisory-completion",)),
        packages=(build_capability_package(),),
    )


def _candidate_snapshots(snapshot: HostSnapshot) -> dict[str, CandidateSet]:
    return {
        f"{dimension}_options": _option_snapshot(
            getattr(snapshot, input_field),
            f"{dimension}_options",
            "jev-harness-preview",
        )
        for dimension, input_field, _ in DIMENSIONS
    }


def preview_snapshot(snapshot: HostSnapshot) -> dict[str, Any]:
    snapshot = snapshot.validated_copy()
    return preview_agent(
        build_definition(),
        task_input=snapshot,
        candidate_sets=_candidate_snapshots(snapshot),
        host_context_keys=("scope",),
    )


class ObservedProvider:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.batches: list[ProviderBatch] = []

    async def evaluate(self, **kwargs: Any) -> ProviderBatch:
        batch = await self.provider.evaluate(**kwargs)
        self.batches.append(batch)
        return batch


def _usage(value: Any) -> dict[str, Any]:
    return {
        "coverage": value.coverage.value,
        "inputTokens": value.input_tokens,
        "outputTokens": value.output_tokens,
        "providerAttempts": value.provider_attempts,
        "submittedQuestions": value.submitted_questions,
    }


def _report(
    snapshot: HostSnapshot,
    *,
    status: str,
    now: datetime,
    ttl_seconds: int,
    model: str,
    recommendation: AdvisoryRecommendation | None = None,
    usage: Mapping[str, Any] | None = None,
    returned_models: Sequence[str] = (),
    reasons: Sequence[str] = (),
) -> AdviceReport:
    return AdviceReport(
        schema_version="1.0.0",
        status=status,
        generated_at=format_timestamp(now),
        expires_at=format_timestamp(now + timedelta(seconds=ttl_seconds)),
        context_digest=context_digest(snapshot.context_id),
        recommendation=recommendation,
        provenance={
            "definitionId": DEFINITION_ID,
            "definitionVersion": DEFINITION_VERSION,
            "capabilityPackage": f"{PACKAGE_ID}@{PACKAGE_VERSION}",
            "frameworkRevision": FRAMEWORK_REVISION,
            "requestedModel": model,
            "returnedModels": list(returned_models),
            "inputSource": "caller-minimized-host-snapshot",
            "authority": "advisory-only",
        },
        usage={} if usage is None else usage,
        guidance={
            action: GUIDANCE[action]
            for action in snapshot.supported_host_actions
            if action in GUIDANCE
        },
        reasons=list(reasons),
    )


async def analyze_snapshot(
    snapshot: HostSnapshot,
    provider: Any,
    *,
    model: str = "jev-1.13.0",
    confidence_threshold: float = MIN_ADVISORY_CONFIDENCE,
    ttl_seconds: int = MAX_REPORT_TTL_SECONDS,
    timeout_seconds: float = 8.0,
    now: datetime | None = None,
) -> AdviceReport:
    snapshot = snapshot.validated_copy()
    current = utc_now() if now is None else now
    if not 0 < ttl_seconds <= MAX_REPORT_TTL_SECONDS or timeout_seconds <= 0:
        raise ValueError("timeouts must be positive and report TTL cannot exceed its maximum")
    if not MIN_ADVISORY_CONFIDENCE <= confidence_threshold <= 1:
        raise ValueError(
            f"confidence threshold must be between {MIN_ADVISORY_CONFIDENCE} and one"
        )
    age = snapshot.age_seconds(current)
    if age > ttl_seconds or age < -30:
        return _report(
            snapshot,
            status="unresolved",
            now=current,
            ttl_seconds=ttl_seconds,
            model=model,
            reasons=("stale_context",),
        )
    missing = [
        input_field
        for _, input_field, _ in DIMENSIONS
        if not getattr(snapshot, input_field)
    ]
    if missing:
        return _report(
            snapshot,
            status="unresolved",
            now=current,
            ttl_seconds=ttl_seconds,
            model=model,
            reasons=tuple(f"missing_options:{field}" for field in missing),
        )
    unsupported_actions = sorted(set(snapshot.supported_host_actions) - set(GUIDANCE))
    if unsupported_actions:
        return _report(
            snapshot,
            status="unresolved",
            now=current,
            ttl_seconds=ttl_seconds,
            model=model,
            reasons=tuple(f"unsupported_host_action:{item}" for item in unsupported_actions),
        )
    if (
        snapshot.context_tokens_used is None
        or snapshot.context_token_capacity is None
        or snapshot.checkpoint_ready is None
    ):
        return _report(
            snapshot,
            status="unresolved",
            now=current,
            ttl_seconds=ttl_seconds,
            model=model,
            reasons=("unknown_compaction_state",),
        )

    observed = ObservedProvider(provider)
    allowed = {
        "difficulty": {item.id for item in snapshot.available_difficulties},
        "reasoning_effort": {item.id for item in snapshot.available_reasoning_efforts},
        "skill": {item.id for item in snapshot.available_skills},
        "compaction": {item.id for item in snapshot.available_compaction_actions},
    }

    def completion_check(value: AdvisoryRecommendation, _: Any) -> bool:
        return (
            value.confidence >= confidence_threshold
            and value.difficulty in allowed["difficulty"]
            and value.reasoning_effort in allowed["reasoning_effort"]
            and value.skill in allowed["skill"]
            and value.compaction in allowed["compaction"]
        )

    runtime = Runtime(
        observed,
        model=model,
        completion_checks={"advisory-completion": completion_check},
    )
    result = await runtime.run(
        build_definition(),
        snapshot,
        RunContext(
            "jev-harness-advice",
            time.monotonic() + timeout_seconds,
            RunLimits(4, 4, 5, 0, 4, 0, 0, 0, 0, 0),
            clock=time.monotonic,
            run_id=f"advice-{context_digest(snapshot.context_id)[:16]}",
        ),
    )
    returned = tuple(
        dict.fromkeys(
            batch.returned_model
            for batch in observed.batches
            if batch.returned_model is not None
        )
    )
    if result.status is TerminalStatus.COMPLETED and isinstance(result.value, AdvisoryRecommendation):
        return _report(
            snapshot,
            status="completed",
            now=current,
            ttl_seconds=ttl_seconds,
            model=model,
            recommendation=result.value,
            usage=_usage(result.usage),
            returned_models=returned,
        )
    reasons = tuple(item.reason.value for item in result.unresolved)
    if result.status is TerminalStatus.FAILED:
        reasons = ("provider_or_runtime_failure",)
    elif not reasons:
        reasons = ("low_confidence_or_policy_rejected",)
    return _report(
        snapshot,
        status="unresolved",
        now=current,
        ttl_seconds=ttl_seconds,
        model=model,
        usage=_usage(result.usage),
        returned_models=returned,
        reasons=reasons,
    )


def typesafe_provider(api_key: str, *, model: str = "jev-1.13.0") -> TypeSafeProvider:
    return TypeSafeProvider(api_key=api_key, default_model=model, max_attempts=1)
