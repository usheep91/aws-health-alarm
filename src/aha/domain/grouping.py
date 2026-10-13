from __future__ import annotations

import hashlib

from aha.domain.due_date import to_local_date
from aha.domain.models import EventSnapshot


def correlation_key(snap: EventSnapshot, tz: str) -> str:
    start = to_local_date(snap.start_time, tz).isoformat() if snap.start_time else "NONE"
    return f"{snap.event_type_code}#{snap.event_region}#{start}"


def group_id_for(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]
