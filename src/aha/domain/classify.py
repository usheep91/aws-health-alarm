from __future__ import annotations

from aha.config import Config
from aha.domain.models import EventSnapshot, Tier

FALLBACK_TIER1_MARKERS = ("PLANNED_LIFECYCLE_EVENT", "_SCHEDULED", "RETIREMENT")


def classify(snap: EventSnapshot, cfg: Config) -> tuple[Tier, str]:
    if snap.event_type_code in cfg.force_track_event_types:
        return Tier.REQUIRED, "FORCED"
    if snap.actionability == "ACTION_REQUIRED":
        return Tier.REQUIRED, "ACTIONABILITY"
    if snap.actionability == "INFORMATIONAL":
        return Tier.EXCLUDED, "ACTIONABILITY"
    if snap.actionability == "ACTION_MAY_BE_REQUIRED":
        tier, source = Tier.REVIEW, "ACTIONABILITY"
    else:
        tier, source = _fallback(snap), "FALLBACK"
    if tier is Tier.REVIEW and snap.event_type_code in cfg.suppress_event_types:
        return Tier.EXCLUDED, "SUPPRESSED"
    return tier, source


def _fallback(snap: EventSnapshot) -> Tier:
    if snap.category == "scheduledChange" or any(m in snap.event_type_code for m in FALLBACK_TIER1_MARKERS):
        return Tier.REQUIRED
    if snap.category == "accountNotification":
        return Tier.REVIEW
    if snap.category == "issue" and snap.entities:
        return Tier.REVIEW
    return Tier.EXCLUDED
