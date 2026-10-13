"""`lineup_assessment_handoff_v1` outcome combination (A10).

Errors win, then a pending roster selection, then any limiting issue; notices never downgrade.
"""

from __future__ import annotations

from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.schema import Outcome


def combine(specs: list[IssueSpec], selection_required: bool) -> Outcome:
    if any(s.severity == "error" for s in specs):
        return "error"
    if selection_required:
        return "selection_required"
    if any(s.severity == "limitation" for s in specs):
        return "partial"
    return "complete"
