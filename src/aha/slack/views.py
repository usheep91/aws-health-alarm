from __future__ import annotations

import json
from datetime import date

from aha.domain.models import Entity, EventRecord, TrackingItem
from aha.slack.blocks import _context, account_label, esc, mrkdwn, plain, status_text

MAX_RESOURCES = 100
SECTION_LIMIT = 2900
MAX_MODAL_BLOCKS = 100


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
    items: list[TrackingItem],
    entities: dict[str, list[Entity]],
    names: dict[str, str | None],
    export_value: str | None = None,
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
    if export_value is not None:
        blocks.insert(
            0,
            {
                "type": "actions",
                "block_id": "resources_export",
                "elements": [
                    {"type": "button", "action_id": "export_resources", "text": plain("CSV 다운로드"), "value": export_value}
                ],
            },
        )
    return {"type": "modal", "title": plain("영향 리소스"), "close": plain("닫기"), "blocks": blocks}


def description_view(event: EventRecord) -> dict:
    head = (
        f"*{esc(event.service)} · {esc(event.event_type_code)}*\n"
        f"리전: {esc(event.event_region)} · 상태: {esc(event.status_code)}"
    )
    text = esc(event.description) if event.description and event.description.strip() else "(설명 없음)"
    lines: list[str] = []
    for line in text.splitlines():
        lines.extend(_hard_split(line) or [""])
    sections = [c for c in _chunks(lines) if c.strip()]
    limit = MAX_MODAL_BLOCKS - 2
    blocks = [{"type": "section", "text": mrkdwn(head)}]
    blocks += [{"type": "section", "text": mrkdwn(c)} for c in sections[:limit]]
    if len(sections) > limit:
        blocks.append(_context("내용이 길어 일부만 표시합니다. 전체 내용은 AWS Health Dashboard에서 확인하세요."))
    return {"type": "modal", "title": plain("이벤트 상세"), "close": plain("닫기"), "blocks": blocks}


def _hard_split(line: str) -> list[str]:
    """SECTION_LIMIT 초과 한 줄을 자른다. 이스케이프 엔티티(&amp; 등) 중간은 자르지 않는다."""
    pieces: list[str] = []
    start = 0
    while start < len(line):
        end = start + SECTION_LIMIT
        if end < len(line):
            tail = line[start:end].rfind("&")
            if tail > 0 and ";" not in line[start + tail : end]:
                end = start + tail
        pieces.append(line[start:end])
        start = end
    return pieces


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
