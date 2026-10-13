from __future__ import annotations

import json
from datetime import date

from aha.domain.models import Entity, TrackingItem
from aha.slack.blocks import account_label, esc, mrkdwn, plain, status_text

MAX_RESOURCES = 100
SECTION_LIMIT = 2900


def due_date_view(meta: dict, initial: date) -> dict:
    return {
        "type": "modal",
        "callback_id": "set_due_modal",
        "private_metadata": json.dumps(meta),
        "title": plain("Due Date 수정"),
        "submit": plain("저장"),
        "close": plain("취소"),
        "blocks": [
            {
                "type": "input",
                "block_id": "due",
                "label": plain("새 Due Date"),
                "element": {"type": "datepicker", "action_id": "value", "initial_date": initial.isoformat()},
            },
            {
                "type": "input",
                "block_id": "reason",
                "label": plain("변경 사유"),
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "max_length": 500},
            },
        ],
    }


def na_view(meta: dict) -> dict:
    return {
        "type": "modal",
        "callback_id": "na_modal",
        "private_metadata": json.dumps(meta),
        "title": plain("해당 없음 처리"),
        "submit": plain("저장"),
        "close": plain("취소"),
        "blocks": [
            {
                "type": "input",
                "block_id": "reason",
                "label": plain("해당 없음 사유"),
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "max_length": 500},
            }
        ],
    }


def resources_view(
    items: list[TrackingItem], entities: dict[str, list[Entity]], names: dict[str, str | None]
) -> dict:
    lines: list[str] = []
    shown = 0
    for item in items:
        lines.append(f"*{account_label(item.account_id, names)}* — {status_text(item)}")
        for entity in entities.get(item.tracking_id, []):
            if shown >= MAX_RESOURCES:
                break
            lines.append(f"• `{esc(entity.value)}` {entity.status}")
            shown += 1
    total = sum(len(v) for v in entities.values())
    if total > shown:
        lines.append(f"외 {total - shown}개 리소스")
    blocks = [{"type": "section", "text": mrkdwn(chunk)} for chunk in _chunks(lines)] or [
        {"type": "section", "text": mrkdwn("(리소스 없음)")}
    ]
    return {"type": "modal", "title": plain("영향 리소스"), "close": plain("닫기"), "blocks": blocks}


def _chunks(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > SECTION_LIMIT:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
