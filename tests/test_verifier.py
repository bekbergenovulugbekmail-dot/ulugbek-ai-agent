"""Verification: the agent must not mark a failed action as done."""

from __future__ import annotations

from ulugbek_ai.agent.verifier import Observation, ToolOutcomeCheck, Verifier
from ulugbek_ai.core.enums import VerificationStatus
from ulugbek_ai.llm.scripted import ScriptedLLMClient, text_response
from tests.factories import verdict_reply


async def test_a_claimed_success_with_only_failed_tools_is_rejected() -> None:
    """The check that stops the agent lying about an action that never happened."""
    verifier = Verifier(checks=[ToolOutcomeCheck()])

    verdict = await verifier.verify(
        goal="Publish the post",
        answer="Done — the post has been published.",
        observations=[Observation("publish_post", False, "HTTP 500 from the API")],
    )

    assert verdict.status is VerificationStatus.FAILURE
    assert "publish_post" in verdict.reason


async def test_a_claimed_success_in_uzbek_is_also_rejected() -> None:
    verifier = Verifier(checks=[ToolOutcomeCheck()])

    verdict = await verifier.verify(
        goal="Post yuborish",
        answer="Post yuborildi.",
        observations=[Observation("send_post", False, "timeout")],
    )

    assert verdict.status is VerificationStatus.FAILURE


async def test_structural_check_defers_when_a_tool_succeeded() -> None:
    check = ToolOutcomeCheck()

    verdict = await check.run(
        goal="Publish",
        answer="Done.",
        plan=None,
        observations=[Observation("publish_post", True, "id=42")],
    )

    assert verdict is None  # defers to the next check


async def test_llm_judge_verdict_is_used() -> None:
    llm = ScriptedLLMClient([verdict_reply("SUCCESS", "the tool returned id=42")])
    verifier = Verifier(llm)

    verdict = await verifier.verify(
        goal="Publish the post",
        answer="Published, id 42.",
        observations=[Observation("publish_post", True, "id=42")],
    )

    assert verdict.is_success
    assert verdict.source == "llm_judge"


async def test_llm_judge_can_reject() -> None:
    llm = ScriptedLLMClient(
        [verdict_reply("FAILURE", "no evidence the post exists", ["a post id"])]
    )
    verifier = Verifier(llm)

    verdict = await verifier.verify(goal="Publish", answer="I think it worked.")

    assert verdict.is_failure
    assert verdict.missing == ["a post id"]


async def test_unparseable_verdict_is_inconclusive_not_a_crash() -> None:
    llm = ScriptedLLMClient([text_response("I could not decide.")])
    verifier = Verifier(llm)

    verdict = await verifier.verify(goal="x", answer="y")

    assert verdict.status is VerificationStatus.INCONCLUSIVE


async def test_a_failing_check_does_not_kill_the_run() -> None:
    class BrokenCheck(ToolOutcomeCheck):
        name = "broken"

        async def run(self, **kwargs):
            raise RuntimeError("check exploded")

    verdict = await Verifier(checks=[BrokenCheck()]).verify(goal="x", answer="y")

    assert verdict.status is VerificationStatus.INCONCLUSIVE
    assert "broken" in verdict.reason


async def test_verification_can_be_disabled() -> None:
    verdict = await Verifier(enabled=False).verify(goal="x", answer="y")
    assert verdict.status is VerificationStatus.SKIPPED


async def test_checks_are_extensible() -> None:
    """Later phases register service-specific checks through this seam."""
    verifier = Verifier(checks=[])
    verifier.register(ToolOutcomeCheck())

    verdict = await verifier.verify(
        goal="Deploy",
        answer="Deployed.",
        observations=[Observation("deploy", False, "build failed")],
    )

    assert verdict.is_failure
