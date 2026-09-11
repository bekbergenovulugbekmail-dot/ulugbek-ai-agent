"""Permission system.

Separate from both the registry and the agent, so the rules can be reasoned
about (and tested) on their own.

Levels, from least to most dangerous: ``READ``, ``WRITE``, ``EXECUTE``,
``DELETE``, ``CRITICAL``. Each level maps to a mode:

* ``auto``     — run immediately.
* ``approval`` — pause the run and ask a human.
* ``deny``     — refuse outright.

The default policy is READ/WRITE automatic, EXECUTE/DELETE by approval, and
``CRITICAL`` **always** by approval: a policy that tries to auto-approve a
critical action is corrected on construction, not trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import PermissionLevel, PermissionMode


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """Outcome of a permission check."""

    allowed: bool
    requires_approval: bool
    level: PermissionLevel
    mode: PermissionMode
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "level": self.level.value,
            "mode": self.mode.value,
            "reason": self.reason,
        }


DEFAULT_POLICY: dict[PermissionLevel, PermissionMode] = {
    PermissionLevel.READ: PermissionMode.AUTO,
    PermissionLevel.WRITE: PermissionMode.AUTO,
    PermissionLevel.EXECUTE: PermissionMode.APPROVAL,
    PermissionLevel.DELETE: PermissionMode.APPROVAL,
    PermissionLevel.CRITICAL: PermissionMode.APPROVAL,
}


@dataclass(slots=True)
class PermissionPolicy:
    """Per-level modes, plus optional per-tool overrides."""

    modes: dict[PermissionLevel, PermissionMode] = field(
        default_factory=lambda: dict(DEFAULT_POLICY)
    )
    #: Tool name -> mode. Overrides the level default for that tool only.
    tool_overrides: dict[str, PermissionMode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for level in PermissionLevel:
            self.modes.setdefault(level, DEFAULT_POLICY[level])
        self._enforce_critical_invariant()

    def _enforce_critical_invariant(self) -> None:
        """A CRITICAL action can never be automatic — only approved or denied."""
        if self.modes[PermissionLevel.CRITICAL] is PermissionMode.AUTO:
            self.modes[PermissionLevel.CRITICAL] = PermissionMode.APPROVAL

    @classmethod
    def from_settings(cls, settings: Settings) -> "PermissionPolicy":
        return cls(modes=dict(settings.permission_policy()))

    def mode_for(
        self, level: PermissionLevel, *, tool_name: str | None = None
    ) -> PermissionMode:
        """Effective mode for a level, honouring per-tool overrides.

        An override may only make a tool *stricter*, never looser — a per-tool
        ``auto`` cannot unlock an action the level itself gates.
        """
        base = self.modes.get(level, DEFAULT_POLICY[level])
        if tool_name and tool_name in self.tool_overrides:
            override = self.tool_overrides[tool_name]
            return _stricter(base, override)
        return base


_MODE_STRICTNESS = {
    PermissionMode.AUTO: 0,
    PermissionMode.APPROVAL: 1,
    PermissionMode.DENY: 2,
}


def _stricter(left: PermissionMode, right: PermissionMode) -> PermissionMode:
    return left if _MODE_STRICTNESS[left] >= _MODE_STRICTNESS[right] else right


class PermissionService:
    """Answers "may this tool run right now?"."""

    def __init__(self, policy: PermissionPolicy | None = None) -> None:
        self._policy = policy or PermissionPolicy()

    @property
    def policy(self) -> PermissionPolicy:
        return self._policy

    def check(
        self,
        level: PermissionLevel,
        *,
        tool_name: str | None = None,
        approved: bool = False,
    ) -> PermissionDecision:
        """Evaluate one action.

        Args:
            level: The tool's permission level.
            tool_name: Used to apply a per-tool override.
            approved: True when a human has already approved *this specific*
                action — the only way an ``approval`` action becomes allowed.
        """
        mode = self._policy.mode_for(level, tool_name=tool_name)

        if mode is PermissionMode.DENY:
            return PermissionDecision(
                allowed=False,
                requires_approval=False,
                level=level,
                mode=mode,
                reason=f"Policy denies all {level.value} actions.",
            )

        if mode is PermissionMode.AUTO:
            return PermissionDecision(
                allowed=True,
                requires_approval=False,
                level=level,
                mode=mode,
                reason=f"{level.value} actions run automatically under this policy.",
            )

        # mode is APPROVAL
        if approved:
            return PermissionDecision(
                allowed=True,
                requires_approval=False,
                level=level,
                mode=mode,
                reason=f"{level.value} action was explicitly approved.",
            )
        return PermissionDecision(
            allowed=False,
            requires_approval=True,
            level=level,
            mode=mode,
            reason=f"{level.value} actions require explicit human approval.",
        )
