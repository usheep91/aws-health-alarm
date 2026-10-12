from aha.domain.grouping import correlation_key, group_id_for
from tests.factories import make_snapshot


def test_correlation_key_uses_local_start_date():
    assert correlation_key(make_snapshot(), "Asia/Seoul") == "AWS_RDS_PLANNED_LIFECYCLE_EVENT#ap-northeast-2#2026-11-30"


def test_correlation_key_without_start():
    assert correlation_key(make_snapshot(start_time=None), "Asia/Seoul").endswith("#NONE")


def test_group_id_is_deterministic_16_hex():
    key = correlation_key(make_snapshot(), "Asia/Seoul")
    assert group_id_for(key) == group_id_for(key)
    assert len(group_id_for(key)) == 16
    int(group_id_for(key), 16)
