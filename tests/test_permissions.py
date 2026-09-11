"""Permission policy behaviour."""

from __future__ import annotations

import pytest

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import PermissionLevel, PermissionMode
from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService


def test_permission_levels_are_ordered() -> None:
    assert PermissionLevel.READ < PermissionLevel.WRITE < PermissionLevel.EXECUTE
    assert PermissionLevel.EXECUTE < PermissionLevel.DELETE < PermissionLevel.CRITICAL


@pytest.mark.parametrize(
    ("level", "expected_auto"),
    [
        (PermissionLevel.READ, True),
        (PermissionLevel.WRITE, True),
        (PermissionLevel.EXECUTE, False),
        (PermissionLevel.DELETE, False),
        (PermissionLevel.CRITICAL, False),
    ],
)
def test_default_policy(level: PermissionLevel, expected_auto: bool) -> None:
    decision = PermissionService().check(level)
    assert decision.allowed is expected_auto
    assert decision.requires_approval is not expected_auto


def test_approval_unlocks_a_gated_action() -> None:
    service = PermissionService()
    assert service.check(PermissionLevel.DELETE).allowed is False
    assert service.check(PermissionLevel.DELETE, approved=True).allowed is True


def test_critical_can_never_be_made_automatic() -> None:
    """The invariant that matters most: CRITICAL always needs a human."""
    policy = PermissionPolicy(
        modes={PermissionLevel.CRITICAL: PermissionMode.AUTO}
    )
    assert policy.modes[PermissionLevel.CRITICAL] is PermissionMode.APPROVAL

    decision = PermissionService(policy).check(PermissionLevel.CRITICAL)
    assert decision.allowed is False
    assert decision.requires_approval is True


def test_critical_may_still_be_denied_outright() -> None:
    policy = PermissionPolicy(modes={PermissionLevel.CRITICAL: PermissionMode.DENY})
    decision = PermissionService(policy).check(
        PermissionLevel.CRITICAL, approved=True
    )
    assert decision.allowed is False
    assert decision.requires_approval is False


def test_denied_mode_ignores_an_approval() -> None:
    policy = PermissionPolicy(modes={PermissionLevel.WRITE: PermissionMode.DENY})
    decision = PermissionService(policy).check(PermissionLevel.WRITE, approved=True)
    assert decision.allowed is False


def test_tool_override_can_only_tighten() -> None:
    policy = PermissionPolicy(
        modes={PermissionLevel.READ: PermissionMode.AUTO},
        tool_overrides={
            "dangerous_reader": PermissionMode.APPROVAL,
            "plain_reader": PermissionMode.AUTO,
        },
    )
    service = PermissionService(policy)

    assert service.check(PermissionLevel.READ, tool_name="plain_reader").allowed
    assert not service.check(
        PermissionLevel.READ, tool_name="dangerous_reader"
    ).allowed

    # An override cannot loosen a gated level.
    loose = PermissionPolicy(
        modes={PermissionLevel.DELETE: PermissionMode.APPROVAL},
        tool_overrides={"rm": PermissionMode.AUTO},
    )
    assert not PermissionService(loose).check(
        PermissionLevel.DELETE, tool_name="rm"
    ).allowed


def test_policy_is_built_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        permission_write=PermissionMode.APPROVAL,
        permission_critical=PermissionMode.AUTO,  # must be corrected
    )
    policy = PermissionPolicy.from_settings(settings)

    assert policy.modes[PermissionLevel.WRITE] is PermissionMode.APPROVAL
    assert policy.modes[PermissionLevel.CRITICAL] is PermissionMode.APPROVAL
