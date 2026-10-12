from datetime import UTC, date, datetime

from aha.domain.models import EntityCounts, EventRecord, Intent, IntentKind, State
from aha.slack.blocks import MAX_ACCOUNT_LINES, account_label, render_notice, render_thread
from tests.factories import ACCOUNT, ARN, TODAY, make_config, make_item

CFG = make_config()
TID = f"g1#{ACCOUNT}"


def _event(**overrides):
    base = dict(
        event_arn=ARN,
        group_id="g1",
        service="RDS",
        event_type_code="AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        category="scheduledChange",
        event_region="ap-northeast-2",
        status_code="upcoming",
        start_time=datetime(2026, 11, 30, tzinfo=UTC),
        end_time=None,
        last_updated_time=datetime(2026, 10, 1, tzinfo=UTC),
        actionability="ACTION_REQUIRED",
        personas=("OPERATIONS",),
        description="Amazon RDS for MySQL 5.7 end of standard support",
        description_hash="h",
        dynamic_status=True,
    )
    base.update(overrides)
    return EventRecord(**base)


def _blocks_of(blocks, block_type):
    return [b for b in blocks if b["type"] == block_type]


def test_open_notice_layout():
    text, blocks = render_notice(_event(), [make_item(counts=EntityCounts(2, 0, 2))], {}, CFG, TODAY)
    assert text == "🟡 [ACTION REQUIRED · P3] RDS · AWS_RDS_PLANNED_LIFECYCLE_EVENT"
    fields = " ".join(f["text"] for f in blocks[1]["fields"])
    assert "2026-11-30 (D-49)" in fields
    assert "⚠️ 담당자 미지정" in fields
    line = next(b for b in blocks if b["type"] == "section" and b.get("text", {}).get("text", "").startswith("• "))
    assert line["text"]["text"] == f"• `{ACCOUNT}`  미조치 2 / 2"
    assert [o["value"] for o in line["accessory"]["options"]] == [f"resolve:{TID}", f"na:{TID}"]
    actions = _blocks_of(blocks, "actions")[0]
    assert actions["block_id"] == "grp:g1"
    assert [e["action_id"] for e in actions["elements"]] == ["assign", "set_due", "resources", "dashboard"]


def test_assignee_is_mentioned():
    _, blocks = render_notice(_event(), [make_item(assignee="U2")], {}, CFG, TODAY)
    assert "<@U2>" in " ".join(f["text"] for f in blocks[1]["fields"])


def test_triage_notice_uses_triage_controls():
    text, blocks = render_notice(_event(), [make_item(state=State.TRIAGE)], {}, CFG, TODAY)
    assert text.startswith("🟡 [검토 필요]")
    assert [e["action_id"] for e in _blocks_of(blocks, "actions")[0]["elements"]] == ["triage_required", "triage_na", "resources"]
    line = next(b for b in blocks if "accessory" in b)
    assert [o["value"] for o in line["accessory"]["options"]] == [f"triage:{TID}", f"na:{TID}"]


def test_security_persona_is_p1():
    text, _ = render_notice(_event(personas=("SECURITY",)), [make_item()], {}, CFG, TODAY)
    assert text.startswith("🔴 [ACTION REQUIRED · P1]")


def test_completed_notice_has_no_controls():
    text, blocks = render_notice(_event(), [make_item(state=State.RESOLVED_AUTO)], {}, CFG, TODAY)
    assert text.startswith("✅ [완료]")
    assert _blocks_of(blocks, "actions") == []
    assert not any("accessory" in b for b in blocks)


def test_manual_completion_hint():
    _, blocks = render_notice(_event(), [make_item(manual_completion_required=True)], {}, CFG, TODAY)
    assert any("자동 완료 판정을 지원하지 않습니다" in b["elements"][0]["text"] for b in _blocks_of(blocks, "context"))


def test_account_lines_are_truncated_within_block_limit():
    items = [make_item(account_id=f"{n:012d}") for n in range(45)]
    _, blocks = render_notice(_event(), items, {}, CFG, TODAY)
    lines = [b for b in blocks if b["type"] == "section" and b.get("text", {}).get("text", "").startswith("• ")]
    assert len(lines) == MAX_ACCOUNT_LINES - 2
    assert any("외 7개 계정" in b["elements"][0]["text"] for b in _blocks_of(blocks, "context"))
    assert len(blocks) <= 50


def test_account_label_with_name():
    assert account_label(ACCOUNT, {ACCOUNT: "prod-payments"}) == f"{ACCOUNT} (prod-payments)"
    assert account_label(ACCOUNT, {}) == ACCOUNT


def test_thread_texts():
    due = Intent(IntentKind.DUE_CHANGED, "g1", TID, {"old": "2026-11-30", "new": "2027-02-28", "source": "AWS", "kept_manual": False})
    assert render_thread(due, {}) == f"{ACCOUNT}: Due Date 변경 2026-11-30 → 2027-02-28 (출처: AWS)"
    assign = Intent(IntentKind.ACTION, "g1", None, {"action": "ASSIGN", "actor": "U1", "accounts": [ACCOUNT], "assignee": "U2", "due": None, "reason": None, "channel": "C1"})
    assert render_thread(assign, {}) == f"<@U1> 님이 {ACCOUNT} 담당자를 <@U2> 님으로 지정했습니다"
    mismatch = Intent(IntentKind.MISMATCH, "g1", TID, {"pending": 2})
    assert "API상 미조치 리소스 2개" in render_thread(mismatch, {})
    assert render_thread(Intent(IntentKind.REFRESH, "g1"), {}) is None


def test_esc_replaces_ampersand_first():
    from aha.slack.blocks import esc

    assert esc("<a> & b") == "&lt;a&gt; &amp; b"


def test_action_reason_is_escaped():
    na = Intent(IntentKind.ACTION, "g1", None, {"action": "NOT_APPLICABLE", "actor": "U1", "accounts": [ACCOUNT], "assignee": None, "due": None, "reason": "<!channel> x & y", "channel": "C1"})
    text = render_thread(na, {})
    assert "&lt;!channel&gt; x &amp; y" in text
    assert "<!channel>" not in text
    assert text.startswith("<@U1> ")
    due = Intent(IntentKind.ACTION, "g1", None, {"action": "SET_DUE", "actor": "U1", "accounts": [ACCOUNT], "assignee": None, "due": "2027-01-01", "reason": "<!here>", "channel": "C1"})
    assert "&lt;!here&gt;" in render_thread(due, {})


def test_description_is_escaped():
    _, blocks = render_notice(_event(description="a < b"), [make_item()], {}, CFG, TODAY)
    texts = [b["text"]["text"] for b in blocks if b["type"] == "section" and "text" in b]
    assert "a &lt; b" in texts


def test_account_name_is_escaped():
    assert account_label(ACCOUNT, {ACCOUNT: "<evil>"}) == f"{ACCOUNT} (&lt;evil&gt;)"


def _action(action):
    return Intent(IntentKind.ACTION, "g1", None, {"action": action, "actor": "U1", "accounts": [ACCOUNT], "assignee": None, "due": None, "reason": None, "channel": "C1"})


def test_triage_required_action_text():
    assert render_thread(_action("TRIAGE_REQUIRED"), {}) == f"<@U1> 님이 {ACCOUNT} '조치 필요'로 판정했습니다. Due Date 추적을 시작합니다"


def test_unknown_action_text_is_generic():
    assert render_thread(_action("SOMETHING_ELSE"), {}) == f"<@U1> 님이 {ACCOUNT} 항목을 갱신했습니다 (SOMETHING_ELSE)"
