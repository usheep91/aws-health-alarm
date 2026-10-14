from __future__ import annotations

from dataclasses import dataclass, field

import yaml

DEFAULT_SLA_DAYS = {"issue": 3, "SECURITY": 7, "default": 30}
DEFAULT_DASHBOARD_URL = "https://health.aws.amazon.com/health/home"
DEFAULT_OWNER_EMAIL_DOMAIN = "amazon.com"
DEFAULT_REMINDER_MILESTONES = (90, 60, 30, 14, 7, 3, 1, 0)
DEFAULT_MENTION_WITHIN_DAYS = 14


@dataclass(frozen=True)
class Config:
    default_channel: str
    timezone: str = "Asia/Seoul"
    shadow_channel: str | None = None
    escalation_channel: str | None = None
    sla_days: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SLA_DAYS))
    force_track_event_types: frozenset[str] = frozenset()
    suppress_event_types: frozenset[str] = frozenset()
    health_dashboard_url: str = DEFAULT_DASHBOARD_URL
    owner_email_domain: str = DEFAULT_OWNER_EMAIL_DOMAIN
    reminder_milestones: tuple[int, ...] = DEFAULT_REMINDER_MILESTONES
    mention_within_days: int = DEFAULT_MENTION_WITHIN_DAYS

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        if not data.get("default_channel"):
            raise ValueError("default_channel is required")
        return cls(
            default_channel=data["default_channel"],
            timezone=data.get("timezone", "Asia/Seoul"),
            shadow_channel=data.get("shadow_channel"),
            escalation_channel=data.get("escalation_channel"),
            sla_days={**DEFAULT_SLA_DAYS, **(data.get("sla_days") or {})},
            force_track_event_types=frozenset(data.get("force_track_event_types") or ()),
            suppress_event_types=frozenset(data.get("suppress_event_types") or ()),
            health_dashboard_url=data.get("health_dashboard_url", DEFAULT_DASHBOARD_URL),
            owner_email_domain=data.get("owner_email_domain", DEFAULT_OWNER_EMAIL_DOMAIN),
            reminder_milestones=tuple(int(m) for m in data.get("reminder_milestones", DEFAULT_REMINDER_MILESTONES)),
            mention_within_days=int(data.get("mention_within_days", DEFAULT_MENTION_WITHIN_DAYS)),
        )

    @classmethod
    def from_yaml(cls, text: str) -> Config:
        return cls.from_dict(yaml.safe_load(text) or {})


def load_config(ssm_client, name: str) -> Config:
    return Config.from_yaml(ssm_client.get_parameter(Name=name)["Parameter"]["Value"])
