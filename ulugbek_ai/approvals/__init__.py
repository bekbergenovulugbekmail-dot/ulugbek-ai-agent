"""Human-in-the-loop approvals.

When the permission policy gates an action, the run does not fail and does not
silently skip the step: it persists an :class:`Approval`, moves the task to
``WAITING_APPROVAL`` and returns. A human decides, and the run is resumed from
exactly where it paused.
"""

from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.approvals.repository import ApprovalRepository

__all__ = ["Approval", "ApprovalManager", "ApprovalRepository"]
