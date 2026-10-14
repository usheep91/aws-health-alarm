from __future__ import annotations

from datetime import date

from aha.config import Config
from aha.domain.due_date import d_label, priority
from aha.domain.models import EventRecord, Intent, IntentKind, State, TrackingItem, split_tracking_id

MAX_ACCOUNT_LINES = 40  # Block Kit 메시지당 50블록 제한 내 유지
SUMMARY_LIMIT = 300
HEADER_LIMIT = 150
PRIORITY_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🟡"}


def esc(text: str) -> str:
    """Slack mrkdwn 제어문자(&, <, >)를 이스케이프한다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plain(text: str) -> dict:
    return {"type": "plain_text", "text": text, "emoji": True}


def mrkdwn(text: str) -> dict:
    return {"type": "mrkdwn", "text": text}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [mrkdwn(text)]}


def _button(text: str, action_id: str, style: str | None = None) -> dict:
    button = {"type": "button", "text": plain(text), "action_id": action_id}
    if style:
        button["style"] = style
    return button


def _option(text: str, value: str) -> dict:
    return {"text": plain(text), "value": value}


def account_label(account_id: str, names: dict[str, str | None]) -> str:
    name = names.get(account_id)
    return f"{account_id} ({esc(name)})" if name else account_id


def status_text(item: TrackingItem) -> str:
    if item.state in (State.OPEN, State.TRIAGE):
        if item.manual_completion_required:
            body = "조치 대기 (수동 완료 필요)"
        else:
            body = f"미조치 {item.counts.pending} / {item.counts.total}"
        return f"검토 대기 · {body}" if item.state is State.TRIAGE else body
    if item.state is State.RESOLVED_AUTO:
        return "✅ 조치 완료(자동)"
    if item.state is State.RESOLVED_MANUAL:
        warn = f" ⚠️ API상 미조치 {item.counts.pending}" if item.verification_mismatch else ""
        return f"✅ 조치 완료(수동){warn}"
    if item.state is State.NOT_APPLICABLE:
        return "⚪ 해당 없음"
    return "⚪ AWS 측 종료"


def render_notice(
    event: EventRecord, items: list[TrackingItem], names: dict[str, str | None], cfg: Config, today: date
) -> tuple[str, list[dict]]:
    title = f"{event.service} · {event.event_type_code}"
    active = [i for i in items if not i.is_terminal]
    if not active:
        header = f"✅ [완료] {title}"
        done = [_header(header), _summary(event), *_description_actions(event), {"type": "divider"}, *_account_blocks(items, names)]
        return header, done

    due = min(i.due_date for i in active)
    triage_only = all(i.state is State.TRIAGE for i in active)
    if triage_only:
        header = f"🟡 [검토 필요] {title}"
    else:
        pri = min(priority(event.personas, i.due_date, today) for i in active if i.state is State.OPEN)
        header = f"{PRIORITY_EMOJI[pri]} [ACTION REQUIRED · {pri}] {title}"

    assignees = sorted({i.assignee for i in active if i.assignee})
    owner = " ".join(f"<@{a}>" for a in assignees) if assignees else "⚠️ 담당자 미지정"
    fields = {
        "type": "section",
        "fields": [
            mrkdwn(f"*Due*\n{due.isoformat()} ({d_label(due, today)})"),
            mrkdwn(f"*리전*\n{event.event_region}"),
            mrkdwn(f"*Persona*\n{', '.join(event.personas) or '-'}"),
            mrkdwn(f"*담당*\n{owner}"),
        ],
    }
    blocks = [
        _header(header),
        fields,
        _summary(event),
        {"type": "divider"},
        *_account_blocks(items, names),
        {"type": "divider"},
        _actions(event, triage_only, cfg),
    ]
    if any(i.manual_completion_required for i in active):
        blocks.append(_context("ℹ️ 이 이벤트는 자동 완료 판정을 지원하지 않습니다. 조치 후 계정별 메뉴에서 '완료 처리'를 눌러 주세요."))
    return header, blocks


def _header(text: str) -> dict:
    return {"type": "header", "text": plain(text[:HEADER_LIMIT])}


def _is_truncated(event: EventRecord) -> bool:
    return len(event.description or "") > SUMMARY_LIMIT


def _description_button() -> dict:
    return _button("전체 내용 보기", "description")


def _description_actions(event: EventRecord) -> list[dict]:
    """완료된 Notice에는 actions 블록이 없으므로 잘린 경우에만 버튼 전용 블록을 둔다."""
    if not _is_truncated(event):
        return []
    return [{"type": "actions", "block_id": f"grp:{event.group_id}", "elements": [_description_button()]}]


def _summary(event: EventRecord) -> dict:
    description = event.description or "(설명 없음)"
    if len(description) > SUMMARY_LIMIT:
        description = description[:SUMMARY_LIMIT] + "…"
    return {"type": "section", "text": mrkdwn(esc(description))}


def _account_blocks(items: list[TrackingItem], names: dict[str, str | None]) -> list[dict]:
    ordered = sorted(items, key=lambda i: (i.is_terminal, i.due_date, i.account_id))
    shown = ordered if len(ordered) <= MAX_ACCOUNT_LINES else ordered[: MAX_ACCOUNT_LINES - 2]
    blocks = []
    for item in shown:
        block = {"type": "section", "text": mrkdwn(f"• `{account_label(item.account_id, names)}`  {status_text(item)}")}
        if not item.is_terminal:
            block["accessory"] = _account_menu(item)
        blocks.append(block)
    if len(shown) < len(ordered):
        blocks.append(_context(f"외 {len(ordered) - len(shown)}개 계정 — [리소스 보기]에서 전체 확인"))
    return blocks


def _account_menu(item: TrackingItem) -> dict:
    first = _option("조치 필요", f"triage:{item.tracking_id}") if item.state is State.TRIAGE else _option("완료 처리", f"resolve:{item.tracking_id}")
    return {
        "type": "overflow",
        "action_id": "account_menu",
        "options": [first, _option("해당 없음", f"na:{item.tracking_id}")],
        "confirm": {
            "title": plain("확인"),
            "text": plain("선택한 처리를 진행할까요?"),
            "confirm": plain("진행"),
            "deny": plain("취소"),
        },
    }


def _actions(event: EventRecord, triage_only: bool, cfg: Config) -> dict:
    if triage_only:
        elements = [
            _button("조치 필요", "triage_required", style="primary"),
            _button("해당 없음", "triage_na"),
            _button("리소스 보기", "resources"),
        ]
    else:
        elements = [
            {"type": "users_select", "action_id": "assign", "placeholder": plain("담당자 지정")},
            _button("Due Date 수정", "set_due"),
            _button("리소스 보기", "resources"),
            {"type": "button", "action_id": "dashboard", "text": plain("Health Dashboard"), "url": cfg.health_dashboard_url},
        ]
    if _is_truncated(event):
        elements.append(_description_button())
    return {"type": "actions", "block_id": f"grp:{event.group_id}", "elements": elements}


def render_thread(intent: Intent, names: dict[str, str | None]) -> str | None:
    label = account_label(split_tracking_id(intent.tracking_id)[1], names) if intent.tracking_id else ""
    d = intent.data
    match intent.kind:
        case IntentKind.NEW:
            return None  # spec 9.3: Notice 갱신에 신규 계정 줄이 이미 표시됨
        case IntentKind.CONTENT_UPDATED:
            return "AWS가 이벤트 내용을 갱신했습니다. 상단 요약을 확인하세요."
        case IntentKind.DUE_CHANGED if d.get("kept_manual"):
            return f"{label}: AWS 변경일 갱신 {d['old'] or '없음'} → {d['new']} (수동 지정한 Due Date 유지)"
        case IntentKind.DUE_CHANGED:
            return f"{label}: Due Date 변경 {d['old']} → {d['new']} (출처: {d['source']})"
        case IntentKind.PROGRESS:
            return f"{label}: 미조치 {d['old_pending']} → {d['new_pending']} / {d['total']}"
        case IntentKind.RESOLVED:
            return f"✅ {label}: 모든 리소스 조치 완료(자동 판정)"
        case IntentKind.REOPENED:
            return f"🔁 {label}: 미조치 리소스가 다시 확인되어 재오픈되었습니다"
        case IntentKind.CLOSED_BY_AWS:
            return f"⚪ {label}: AWS 측 이벤트가 종료되었습니다. 조치 여부를 확인하세요"
        case IntentKind.MISMATCH:
            return f"⚠️ {label}: 수동 완료 후 72시간이 지났지만 API상 미조치 리소스 {d['pending']}개가 남아 있습니다"
        case IntentKind.PROMOTED:
            return f"⬆️ {label}: ACTION_REQUIRED로 상향되어 Due Date 추적을 시작합니다"
        case IntentKind.ACTION:
            return _action_text(d, names)
        case IntentKind.REMINDER:
            return _reminder_text(d, names)
        case IntentKind.EXPORT:
            return None
        case _:
            return None


def _reminder_text(d: dict, names: dict[str, str | None]) -> str:
    accounts = ", ".join(account_label(a, names) for a in d["accounts"])
    label = d["label"]
    head = f"🚨 기한 경과 {label}" if label.startswith("D+") else f"⏰ {label}"
    text = f"{head} · 미조치 계정 {len(d['accounts'])}개: {accounts} (기한 {d['due']})"
    if d.get("mention"):
        assignees = d.get("assignees") or []
        text += (" " + " ".join(f"<@{u}>" for u in assignees)) if assignees else " ⚠️ 담당자 미지정"
    return text


def _action_text(d: dict, names: dict[str, str | None]) -> str:
    if d["action"] == "AUTO_ASSIGN":
        pairs = ", ".join(f"{account_label(a, names)} → <@{d['assignments'][a]}>" for a in d["accounts"])
        return f"계정 이메일 기준으로 담당자를 자동 지정했습니다: {pairs}"
    accounts = ", ".join(account_label(a, names) for a in d["accounts"])
    actor = f"<@{d['actor']}>"
    match d["action"]:
        case "ASSIGN":
            return f"{actor} 님이 {accounts} 담당자를 <@{d['assignee']}> 님으로 지정했습니다"
        case "SET_DUE":
            return f"{actor} 님이 {accounts} Due Date를 {d['due']}로 변경했습니다 (사유: {esc(str(d['reason']))})"
        case "RESOLVE_MANUAL":
            return f"{actor} 님이 {accounts} 조치 완료 처리했습니다"
        case "NOT_APPLICABLE":
            return f"{actor} 님이 {accounts} '해당 없음' 처리했습니다 (사유: {esc(str(d['reason']))})"
        case "TRIAGE_REQUIRED":
            return f"{actor} 님이 {accounts} '조치 필요'로 판정했습니다. Due Date 추적을 시작합니다"
        case _:
            return f"{actor} 님이 {accounts} 항목을 갱신했습니다 ({d['action']})"
