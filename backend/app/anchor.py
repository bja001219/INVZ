"""The scene anchor gate, as one pure decision.

Cuts after the first stay visually consistent by referencing the scene's anchor: the selected
image of Cut 1. Deciding *when* a cut may run without that anchor is the whole of character
consistency, so the rule lives in one function that both the worker (which enforces it) and the
scene response (which explains it to the user) call with the same inputs.
"""

from enum import StrEnum

ANCHOR_CUT_ORDER = 1


class AnchorDecision(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """Cut 1 itself, or a cut whose scene has no Cut 1 to anchor against."""

    REFERENCE = "REFERENCE"
    """The anchor image exists; submit with it attached."""

    WAIT = "WAIT"
    """The anchor can still arrive. Running now would silently break consistency."""

    PROCEED_UNANCHORED = "PROCEED_UNANCHORED"
    """The anchor can no longer arrive on its own. Run without it rather than stall forever."""


def decide_anchor(
    *,
    cut_order: int,
    anchor_cut_exists: bool,
    anchor_image_ready: bool,
    anchor_job_active: bool,
    anchor_job_failed: bool,
) -> AnchorDecision:
    """Decide whether one IMAGE job may run now, and whether it carries the anchor.

    Waiting is the default whenever the anchor is missing, including when Cut 1 was never
    requested at all: a cut generated outside a batch must not quietly skip the anchor. The
    only release is Cut 1 having exhausted its retries, which is the one case where waiting
    could never end on its own.
    """
    if cut_order == ANCHOR_CUT_ORDER or not anchor_cut_exists:
        return AnchorDecision.NOT_APPLICABLE
    if anchor_image_ready:
        return AnchorDecision.REFERENCE
    if anchor_job_active:
        return AnchorDecision.WAIT
    if anchor_job_failed:
        return AnchorDecision.PROCEED_UNANCHORED
    return AnchorDecision.WAIT
