import boto3
import pytest
from moto import mock_aws

from aha.config import Config, load_config


def test_from_yaml_merges_sla_defaults_and_freezes_lists():
    cfg = Config.from_yaml(
        "default_channel: C0DEFAULT\n"
        "sla_days: {issue: 2}\n"
        "suppress_event_types: [AWS_EC2_X]\n"
    )
    assert cfg.default_channel == "C0DEFAULT"
    assert cfg.timezone == "Asia/Seoul"
    assert cfg.sla_days == {"issue": 2, "SECURITY": 7, "default": 30}
    assert cfg.suppress_event_types == frozenset({"AWS_EC2_X"})
    assert cfg.force_track_event_types == frozenset()


def test_default_channel_is_required():
    with pytest.raises(ValueError, match="default_channel"):
        Config.from_dict({})


@mock_aws
def test_load_config_from_ssm():
    ssm = boto3.client("ssm", region_name="ap-northeast-2")
    ssm.put_parameter(Name="/aha/dev/config", Type="String", Value="default_channel: C0DEFAULT\nshadow_channel: C0SHADOW\n")
    cfg = load_config(ssm, "/aha/dev/config")
    assert cfg.shadow_channel == "C0SHADOW"
