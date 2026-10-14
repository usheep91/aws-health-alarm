import json
from datetime import date

from aha.domain.models import Entity
from aha.slack.views import MAX_RESOURCES, due_date_view, na_view, resources_view
from tests.factories import ACCOUNT, make_item


def test_due_date_view():
    view = due_date_view({"tracking_ids": ["t1"], "channel": "C1"}, date(2026, 11, 30))
    assert view["callback_id"] == "set_due_modal"
    assert json.loads(view["private_metadata"]) == {"tracking_ids": ["t1"], "channel": "C1"}
    assert view["blocks"][0]["block_id"] == "due"
    assert view["blocks"][0]["element"]["initial_date"] == "2026-11-30"
    assert view["blocks"][1]["block_id"] == "reason"


def test_na_view():
    view = na_view({"tracking_ids": ["t1"], "channel": "C1"})
    assert view["callback_id"] == "na_modal"
    assert view["blocks"][0]["block_id"] == "reason"


def test_resources_view_caps_entities():
    item = make_item()
    entities = {item.tracking_id: [Entity(f"db-{n}", "PENDING") for n in range(MAX_RESOURCES + 5)]}
    view = resources_view([item], entities, {})
    text = "\n".join(b["text"]["text"] for b in view["blocks"])
    assert ACCOUNT in text
    assert "`db-99` PENDING" in text
    assert "`db-100`" not in text
    assert "외 5개 리소스" in text
    assert all(len(b["text"]["text"]) <= 3000 for b in view["blocks"])


def test_reason_inputs_have_max_length():
    due = due_date_view({"tracking_ids": ["t1"], "channel": "C1"}, date(2026, 11, 30))
    assert due["blocks"][1]["element"]["max_length"] == 500
    assert na_view({"tracking_ids": ["t1"], "channel": "C1"})["blocks"][0]["element"]["max_length"] == 500


def test_resources_view_escapes_entity_values():
    item = make_item()
    view = resources_view([item], {item.tracking_id: [Entity("<!channel>&x", "PENDING")]}, {})
    text = "\n".join(b["text"]["text"] for b in view["blocks"])
    assert "`&lt;!channel&gt;&amp;x`" in text


def test_resources_view_never_emits_empty_section():
    from aha.slack.views import _chunks

    chunks = _chunks(["x" * 3000])
    assert chunks == ["x" * 3000]
    assert all(c for c in _chunks(["a", "y" * 3000, "b"]))


def test_resources_view_export_button_is_first_block():
    item = make_item()
    view = resources_view([item], {}, {}, export_value='{"group_id": "g1", "channel": "C1"}')
    first = view["blocks"][0]
    assert first["type"] == "actions"
    assert first["block_id"] == "resources_export"
    button = first["elements"][0]
    assert (button["action_id"], button["text"]["text"]) == ("export_resources", "CSV 다운로드")
    assert button["value"] == '{"group_id": "g1", "channel": "C1"}'


def test_resources_view_without_export_value_has_no_button():
    view = resources_view([make_item()], {}, {})
    assert all(b["type"] != "actions" for b in view["blocks"])


def _event(description):
    from tests.test_blocks import _event as make_event

    return make_event(description=description)


def _texts(view):
    return [b["text"]["text"] for b in view["blocks"] if b["type"] == "section"]


def test_description_view_short():
    from aha.slack.views import description_view

    view = description_view(_event("짧은 설명"))
    assert view["type"] == "modal"
    assert view["title"]["text"] == "이벤트 상세"
    assert view["close"]["text"] == "닫기"
    assert len(view["blocks"]) == 2
    assert view["blocks"][0]["text"]["text"] == "*RDS · AWS_RDS_PLANNED_LIFECYCLE_EVENT*\n리전: ap-northeast-2 · 상태: upcoming"
    assert view["blocks"][1]["text"]["text"] == "짧은 설명"


def test_description_view_splits_long_multiline_text():
    from aha.slack.views import SECTION_LIMIT, description_view

    text = "\n".join(f"line {n} " + "x" * 60 for n in range(100))
    assert len(text) > 6500
    sections = _texts(description_view(_event(text)))[1:]
    assert len(sections) > 1
    assert all(0 < len(s) <= SECTION_LIMIT for s in sections)
    assert "\n".join(sections) == text


def test_description_view_hard_splits_single_long_line():
    from aha.slack.views import SECTION_LIMIT, description_view

    text = "a" * 6000
    sections = _texts(description_view(_event(text)))[1:]
    assert len(sections) == 3
    assert all(0 < len(s) <= SECTION_LIMIT for s in sections)
    assert "".join(sections) == text


def test_description_view_escapes_mrkdwn():
    from aha.slack.views import description_view

    sections = _texts(description_view(_event("<!channel> & more")))
    assert sections[1] == "&lt;!channel&gt; &amp; more"


def test_description_view_caps_blocks():
    from aha.slack.views import MAX_MODAL_BLOCKS, description_view

    view = description_view(_event("\n".join("y" * 2000 for _ in range(500))))
    assert len(view["blocks"]) <= MAX_MODAL_BLOCKS
    last = view["blocks"][-1]
    assert last["type"] == "context"
    assert "AWS Health Dashboard" in last["elements"][0]["text"]


def test_description_view_without_description():
    from aha.slack.views import description_view

    assert _texts(description_view(_event(None)))[1] == "(설명 없음)"


def test_description_view_hard_split_keeps_entities_whole():
    from aha.slack.views import SECTION_LIMIT, description_view

    for prefix in ("a", "ab", "abc", "abcd"):  # 2900이 5의 배수라 접두어로 경계를 어긋나게 한다
        sections = _texts(description_view(_event(prefix + "&" * 3000)))[1:]
        assert len(sections) > 1
        assert all(0 < len(s) <= SECTION_LIMIT for s in sections)
        assert all(s.endswith(";") for s in sections)
        assert all(s.startswith("&amp;") for s in sections[1:])
        assert "".join(sections) == prefix + "&amp;" * 3000


def test_description_view_blank_description_is_placeholder():
    from aha.slack.views import description_view

    for blank in (" " * 5000, "\n\n  \n"):
        assert _texts(description_view(_event(blank)))[1:] == ["(설명 없음)"]
