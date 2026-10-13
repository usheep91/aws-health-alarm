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
