from __future__ import annotations

import csv
import io
from datetime import date

from aha.domain.models import Entity, EventRecord, TrackingItem

COLUMNS = ["account_id", "account_name", "state", "due_date", "event_type", "region", "resource", "resource_status"]
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: str) -> str:
    """스프레드시트 수식 주입 방지: 수식 문자로 시작하는 셀 앞에 작은따옴표를 붙인다."""
    return f"'{value}" if value.lstrip().startswith(FORMULA_PREFIXES) else value


def build_resource_csv(
    event: EventRecord,
    items: list[TrackingItem],
    entities: dict[str, list[Entity]],
    names: dict[str, str | None],
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for item in sorted(items, key=lambda i: i.account_id):
        base = [
            item.account_id,
            names.get(item.account_id) or "",
            item.state.value,
            item.due_date.isoformat(),
            event.event_type_code,
            event.event_region,
        ]
        rows = [(e.value, e.status) for e in entities.get(item.tracking_id, [])] or [("", "")]
        for resource, status in rows:
            writer.writerow([_safe(str(c)) for c in (*base, resource, status)])
    return buf.getvalue().encode("utf-8-sig")


def export_filename(event: EventRecord, today: date) -> str:
    return f"{event.event_type_code}_{event.event_region}_{today:%Y%m%d}.csv"
