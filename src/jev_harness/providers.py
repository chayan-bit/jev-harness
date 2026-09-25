"""Provider wrappers: an offline scripted double and a metered live gate.

Both expose the Jev-Frame provider shape ``async evaluate(**kwargs) -> ProviderBatch``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

from jev_frame import (
    AttemptStatus,
    ChoiceAnswer,
    ProviderAttempt,
    ProviderBatch,
    TypeSafeProvider,
    Usage,
    UsageCoverage,
)

from .records import BudgetExceeded, ContractError, UnsupportedCapability

DEFAULT_MODEL = "jev-1.13.0"


class OfflineChoiceProvider:
    """Scripted offline provider: picks the first option (or the last, for ``no_fit``) of every question.

    It never touches the network and declares ``live = False``, so ``Decisions`` accepts it.
    """

    live = False

    def __init__(self, *, confidence: float = 0.9, no_fit: bool = False, fail: bool = False,
                 model: str = DEFAULT_MODEL) -> None:
        self.confidence = confidence
        self.no_fit = no_fit
        self.fail = fail
        self.model = model
        self.calls = 0
        self.questions: list[Any] = []

    async def evaluate(self, **kwargs: Any) -> ProviderBatch:
        self.calls += 1
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        self.questions.extend(kwargs["questions"])
        answers = {}
        for question in kwargs["questions"]:
            option = question.options[-1] if self.no_fit else question.options[0]
            answers[question.routing_id] = ChoiceAnswer(
                option.key,
                {candidate.key: (1.0 if candidate.key == option.key else 0.0) for candidate in question.options},
                self.confidence,
            )
        return ProviderBatch(
            answers, kwargs["requested_model"], self.model, "offline-request",
            Usage(UsageCoverage.COMPLETE, 20, 8, 1, len(answers)),
            (ProviderAttempt("offline-attempt", 1, AttemptStatus.SUCCEEDED, request_id="offline-request"),),
        )

    async def aclose(self) -> None:
        return None


class MeteredJevProvider:
    """Live Jev gate: a model catalog, one question per dispatch and a hard question ceiling.

    ``is_qualified`` lets the host revoke the gate (for example when an operator approval expires);
    it is checked when ``Decisions`` is built and before every dispatch.
    The API key is read once from the named environment variable and never stored in records.
    """

    live = True

    def __init__(
        self,
        *,
        models: Sequence[str] = (DEFAULT_MODEL,),
        max_questions: int,
        api_key_env: str = "TYPESAFE_API_KEY",
        inner: Any = None,
        is_qualified: Callable[[], bool] = lambda: True,
    ) -> None:
        if not models or max_questions < 0:
            raise ContractError("metered provider needs a model catalog and a nonnegative ceiling")
        self.models = tuple(models)
        self.max_questions = max_questions
        self.questions = 0
        self.tokens = {"input": 0, "output": 0, "unknown_usage_calls": 0}
        self._is_qualified = is_qualified
        if inner is None:
            key = os.environ.get(api_key_env)
            if not key:
                raise UnsupportedCapability("the named Jev key variable is not set")
            inner = TypeSafeProvider(api_key=key, default_model=self.models[0], max_attempts=1)
        self.inner = inner

    @property
    def qualified(self) -> bool:
        return bool(self._is_qualified())

    async def evaluate(self, **kwargs: Any) -> ProviderBatch:
        if not self.qualified:
            raise UnsupportedCapability("Jev provider qualification was revoked")
        if kwargs.get("requested_model") not in self.models:
            raise UnsupportedCapability("Jev model is outside the qualified catalog")
        if len(kwargs.get("questions", ())) != 1:
            raise ContractError("one question per qualified Jev dispatch")
        if self.questions + 1 > self.max_questions:
            raise BudgetExceeded("qualified Jev question ceiling reached")
        self.questions += 1  # charged before the request leaves the host
        batch: ProviderBatch = await self.inner.evaluate(**kwargs)
        usage = batch.usage
        if usage.input_tokens is None or usage.output_tokens is None:
            self.tokens["unknown_usage_calls"] += 1
        else:
            self.tokens["input"] += usage.input_tokens
            self.tokens["output"] += usage.output_tokens
        return batch

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close:
            await close()
