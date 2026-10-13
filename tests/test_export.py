import csv
import io
from datetime import date

from aha.domain.models import Entity
from aha.slack.export import COLUMNS, build_resource_csv, export_filename
from tests.factories import ACCOUNT, OTHER_ACCOUNT, make_item


def _event():
    from aha.domain.grouping import correlation_key, group_id_for
    from tests.factories import NOW, make_config, make_snapshot
    from tests.fakes import InMemoryRepository
    from aha.domain.observe import observe

    repo = InMemoryRepository()
    observe(make_snapshot(), repo, make_config(), NOW)
    gid = group_id_for(correlation_key(make_snapshot(), make_config().timezone))
    return repo.get_event(repo.get_group(gid).primary_arn)


def _rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_header_and_bom():
    data = build_resource_csv(_event(), [], {}, {})
    assert data.startswith(b"\xef\xbb\xbf")
    assert _rows(data) == [COLUMNS]
    assert COLUMNS == ["account_id", "account_name", "state", "due_date", "event_type", "region", "resource", "resource_status"]


def test_one_row_per_entity_sorted_by_account():
    a, b = make_item(account_id=OTHER_ACCOUNT), make_item(account_id=ACCOUNT)
    entities = {
        a.tracking_id: [Entity("x-1", "PENDING")],
        b.tracking_id: [Entity("db-1", "PENDING"), Entity("db-2", "RESOLVED")],
    }
    event = _event()
    rows = _rows(build_resource_csv(event, [a, b], entities, {}))[1:]
    assert [r[0] for r in rows] == sorted([ACCOUNT, ACCOUNT, OTHER_ACCOUNT])
    assert rows[0][1:] == [
        "", b.state.value, b.due_date.isoformat(), event.event_type_code, event.event_region, "db-1", "PENDING"
    ]
    assert [r[6] for r in rows] == ["db-1", "db-2", "x-1"]


def test_item_without_entities_gets_empty_row():
    item = make_item()
    rows = _rows(build_resource_csv(_event(), [item], {}, {}))[1:]
    assert len(rows) == 1
    assert rows[0][6:] == ["", ""]


def test_korean_account_name_round_trips():
    item = make_item()
    rows = _rows(build_resource_csv(_event(), [item], {}, {ACCOUNT: "운영 계정"}))[1:]
    assert rows[0][1] == "운영 계정"


def test_formula_injection_is_neutralised():
    item = make_item()
    entities = {item.tracking_id: [Entity('=HYPERLINK("x")', "PENDING")]}
    rows = _rows(build_resource_csv(_event(), [item], entities, {}))[1:]
    assert rows[0][6] == '\'=HYPERLINK("x")'


def test_filename():
    assert export_filename(_event(), date(2026, 10, 13)) == "AWS_RDS_PLANNED_LIFECYCLE_EVENT_ap-northeast-2_20261013.csv"


def test_leading_whitespace_formula_is_neutralised():
    item = make_item()
    entities = {item.tracking_id: [Entity(" =cmd", "PENDING")]}
    rows = _rows(build_resource_csv(_event(), [item], entities, {}))[1:]
    assert rows[0][6] == "' =cmd"
