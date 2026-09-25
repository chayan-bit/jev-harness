"""Attachable Jev elements for any agent harness.

- Advisory: ``HostSnapshot`` -> ``analyze_snapshot`` -> advisory-only ``AdviceReport``.
- Hooks: ``render_hook`` turns a report into an official Codex hook response.
- Shadow judgments: ``Decisions`` over the ports in ``jev_harness.ports``.
- Calibration: ``corpus``, ``freeze``, ``held_out`` and ``pilot_report``.

No element grants execution or permission authority.
"""

from .agent import FRAMEWORK_REVISION, analyze_snapshot, build_definition, preview_snapshot, typesafe_provider
from .decisions import FAMILIES, Decisions, definition, reject_secret_indicators
from .evaluation import (
    FrozenStudy,
    capture,
    corpus,
    freeze,
    held_out,
    pilot_report,
    replay,
    split_for_group,
    threshold_policy,
    wilson_upper,
)
from .hooks import DenyPolicy, render_hook
from .memory import AuditEvent, InMemoryAccountant, InMemoryDecisionStore, InMemoryScope, QuestionCall
from .models import AdviceReport, AdvisoryRecommendation, HostOption, HostSnapshot, InputError, context_digest
from .ports import CandidateCatalog, DecisionScope, DecisionStore, JevAccountant, ScopeGrant, StoredDecision
from .providers import MeteredJevProvider, OfflineChoiceProvider
from .records import (
    AdviceV2,
    BudgetExceeded,
    CandidateProbability,
    CapturedDecision,
    ContractError,
    DecisionCandidate,
    DecisionLabel,
    DecisionOutcome,
    DecisionPolicy,
    DecisionRequest,
    IdempotencyConflict,
    PermissionDenied,
    PilotRun,
    Record,
    SourceRecord,
    SubjectBinding,
    UnsupportedCapability,
    canonical_json,
    digest,
    parse_record,
)

__version__ = "0.1.1"  # x-release-please-version

__all__ = [
    "FAMILIES",
    "FRAMEWORK_REVISION",
    "AdviceReport",
    "AdviceV2",
    "AdvisoryRecommendation",
    "AuditEvent",
    "BudgetExceeded",
    "CandidateCatalog",
    "CandidateProbability",
    "CapturedDecision",
    "ContractError",
    "DecisionCandidate",
    "DecisionLabel",
    "DecisionOutcome",
    "DecisionPolicy",
    "DecisionRequest",
    "DecisionScope",
    "DecisionStore",
    "Decisions",
    "DenyPolicy",
    "FrozenStudy",
    "HostOption",
    "HostSnapshot",
    "IdempotencyConflict",
    "InMemoryAccountant",
    "InMemoryDecisionStore",
    "InMemoryScope",
    "InputError",
    "JevAccountant",
    "MeteredJevProvider",
    "OfflineChoiceProvider",
    "PermissionDenied",
    "PilotRun",
    "QuestionCall",
    "Record",
    "ScopeGrant",
    "SourceRecord",
    "StoredDecision",
    "SubjectBinding",
    "UnsupportedCapability",
    "__version__",
    "analyze_snapshot",
    "build_definition",
    "canonical_json",
    "capture",
    "context_digest",
    "corpus",
    "definition",
    "digest",
    "freeze",
    "held_out",
    "parse_record",
    "pilot_report",
    "preview_snapshot",
    "reject_secret_indicators",
    "render_hook",
    "replay",
    "split_for_group",
    "threshold_policy",
    "typesafe_provider",
    "wilson_upper",
]
