"""Verification.

An action is never assumed to have worked. Two layers run, cheapest first:

1. **Structural** — did the tools actually succeed? A final answer that claims
   an effect while every tool call failed is rejected without spending a token.
2. **Semantic** — an LLM judge compares the goal, the evidence and the proposed
   answer, and returns SUCCESS / FAILURE / INCONCLUSIVE with a reason.

Later phases register additional checks (a GitHub commit exists, a Railway
deployment is live, a Telegram message was delivered) by implementing
:class:`VerificationCheck`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ulugbek_ai.agent.prompts import VERIFIER_SYSTEM
from ulugbek_ai.core.enums import VerificationStatus
from ulugbek_ai.core.redaction import truncate
from ulugbek_ai.llm.base import LLMClient, LLMMessage
from ulugbek_ai.tasks.schemas import Plan

logger = logging.getLogger(__name__)

VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["SUCCESS", "FAILURE", "INCONCLUSIVE"],
            "description": "Was the goal actually achieved?",
        },
        "reason": {
            "type": "string",
            "description": "One or two sentences citing the evidence.",
        },
        "missing": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What is still needed, when the status is not SUCCESS.",
        },
    },
    "required": ["status", "reason", "missing"],
    "additionalProperties": False,
}

_MAX_EVIDENCE_CHARS = 6_000


@dataclass(slots=True)
class VerificationResult:
    """Verdict on whether a run achieved its goal."""

    status: VerificationStatus
    reason: str
    missing: list[str] = field(default_factory=list)
    source: str = "verifier"

    @property
    def is_success(self) -> bool:
        return self.status is VerificationStatus.SUCCESS

    @property
    def is_failure(self) -> bool:
        return self.status is VerificationStatus.FAILURE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "missing": self.missing,
            "source": self.source,
        }


@dataclass(slots=True)
class Observation:
    """One recorded tool outcome, as seen by the verifier."""

    tool_name: str
    ok: bool
    summary: str


class VerificationCheck(ABC):
    """A single verification rule."""

    name: str = "check"

    @abstractmethod
    async def run(
        self,
        *,
        goal: str,
        answer: str,
        plan: Plan | None,
        observations: list[Observation],
    ) -> VerificationResult | None:
        """Return a verdict, or ``None`` to defer to the next check."""


class ToolOutcomeCheck(VerificationCheck):
    """Rejects a claimed success that no tool result supports."""

    name = "tool_outcome"

    #: Wording that asserts an external effect took place.
    _EFFECT_CLAIMS = (
        "done", "created", "deployed", "published", "sent", "deleted",
        "updated", "committed", "pushed", "saved", "bajarildi", "yaratildi",
        "yuborildi", "o'chirildi", "saqlandi",
    )

    async def run(
        self,
        *,
        goal: str,
        answer: str,
        plan: Plan | None,
        observations: list[Observation],
    ) -> VerificationResult | None:
        if not observations:
            return None

        failed = [item for item in observations if not item.ok]
        succeeded = [item for item in observations if item.ok]

        if failed and not succeeded:
            lowered = answer.lower()
            if any(claim in lowered for claim in self._EFFECT_CLAIMS):
                return VerificationResult(
                    status=VerificationStatus.FAILURE,
                    reason=(
                        "The answer claims an action was carried out, but every "
                        f"tool call failed ({', '.join(item.tool_name for item in failed)})."
                    ),
                    missing=[item.summary for item in failed],
                    source=self.name,
                )
        return None


class LLMJudgeCheck(VerificationCheck):
    """Asks the model to judge goal completion against the evidence."""

    name = "llm_judge"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def run(
        self,
        *,
        goal: str,
        answer: str,
        plan: Plan | None,
        observations: list[Observation],
    ) -> VerificationResult | None:
        evidence = (
            "\n".join(
                f"- {item.tool_name}: {'OK' if item.ok else 'FAILED'} — {item.summary}"
                for item in observations
            )
            or "- (no tools were used)"
        )
        plan_text = (
            "\n".join(
                f"{step.index + 1}. [{step.status}] {step.description}"
                for step in plan.steps
            )
            if plan and plan.steps
            else "(no explicit plan)"
        )

        prompt = (
            f"<goal>\n{goal}\n</goal>\n\n"
            f"<plan>\n{plan_text}\n</plan>\n\n"
            f"<tool_evidence>\n{truncate(evidence, _MAX_EVIDENCE_CHARS)}\n</tool_evidence>\n\n"
            f"<proposed_answer>\n{truncate(answer, _MAX_EVIDENCE_CHARS)}\n</proposed_answer>"
        )

        response = await self._llm.complete(
            [LLMMessage.user(prompt)],
            system=VERIFIER_SYSTEM,
            response_schema=VERIFICATION_SCHEMA,
            max_tokens=1_500,
        )
        try:
            payload = response.json()
        except json.JSONDecodeError:
            logger.warning("Verifier returned non-JSON output; treating as inconclusive")
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason="The verifier did not return a parseable verdict.",
                source=self.name,
            )

        try:
            status = VerificationStatus(str(payload.get("status", "")).upper())
        except ValueError:
            status = VerificationStatus.INCONCLUSIVE

        missing = payload.get("missing") or []
        return VerificationResult(
            status=status,
            reason=str(payload.get("reason", "")).strip(),
            missing=[str(item) for item in missing if str(item).strip()],
            source=self.name,
        )


class Verifier:
    """Runs the registered checks in order and returns the first verdict."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        checks: list[VerificationCheck] | None = None,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        if checks is not None:
            self._checks = checks
        else:
            self._checks = [ToolOutcomeCheck()]
            if llm is not None:
                self._checks.append(LLMJudgeCheck(llm))

    def register(self, check: VerificationCheck) -> None:
        """Add a check (later phases plug service-specific ones in here)."""
        self._checks.append(check)

    async def verify(
        self,
        *,
        goal: str,
        answer: str,
        plan: Plan | None = None,
        observations: list[Observation] | None = None,
    ) -> VerificationResult:
        """Decide whether *answer* actually achieves *goal*."""
        if not self._enabled:
            return VerificationResult(
                status=VerificationStatus.SKIPPED,
                reason="Verification is disabled by configuration.",
                source="config",
            )

        observed = observations or []
        for check in self._checks:
            try:
                verdict = await check.run(
                    goal=goal, answer=answer, plan=plan, observations=observed
                )
            except Exception as exc:  # noqa: BLE001 - a check must not kill the run
                logger.exception("Verification check %s raised", check.name)
                return VerificationResult(
                    status=VerificationStatus.INCONCLUSIVE,
                    reason=f"Verification check '{check.name}' errored: {exc}",
                    source=check.name,
                )
            if verdict is not None:
                return verdict

        return VerificationResult(
            status=VerificationStatus.INCONCLUSIVE,
            reason="No verification check produced a verdict.",
        )
