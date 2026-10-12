# AWS Health Action Required → Slack 추적 P1(MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Health Organizational view API를 주기적으로 대사(reconcile)하여 조치 필요 이벤트를 계정 단위로 추적하고, 섀도 Slack 채널에 이벤트 그룹당 1개 메시지로 게시하며 Slack 버튼으로 담당 지정·Due Date 수정·완료/해당 없음 처리를 할 수 있는 MVP를 만든다.

**Architecture:** 순수 도메인 로직(`aha.domain`)이 분류·Due Date·상태 전이·그룹 연결을 담당하고, 모든 경로(Reconcile, Slack Interaction)는 `observe()`/`apply_group_action()`만 호출한다. 상태 변경은 Intent로 SQS FIFO(MessageGroupId=groupId)에 발행되고 Notifier가 Slack 메시지를 게시/수정한다. 인프라는 Terraform `core` 모듈(DynamoDB 단일 테이블, SQS FIFO, Lambda 3개, EventBridge Scheduler, API Gateway HTTP API, CloudWatch 알람)로 구성한다.

**Tech Stack:** Python 3.12, boto3, aws-lambda-powertools 3.x, slack_sdk, PyYAML, pytest, moto 5, Terraform ≥ 1.6, AWS provider ≥ 5.40

**Spec:** `docs/specs/2026-10-12-health-action-required-slack-design.md`

**P1 범위 밖 (후속 Plan):**
- P2: Organizations 계정 태그 라우팅(`OrgTagRouter`), persona 멘션, 마일스톤 리마인더·에스컬레이션, TRIAGE D-14 자동 승격, 주간 다이제스트, 계정 해지/정지 시 자동 NOT_APPLICABLE, PROGRESS 스레드 sweep 단위 묶음(spec 9.3), `UnnotifiedTier1Items` 알람(spec 10), 사용자 피드백(ephemeral) 응답, 기존 Notice 채널 오류 다이제스트 노출, SQS KMS(CMK) 암호화 및 API 액세스 로그
- P3: 리전별 EventBridge forwarder, Ingest Lambda, `aha-dedup` 테이블

## Global Constraints

- 수집 계정: Health 위임 관리자 계정. 관리 계정에는 배포하지 않는다.
- 홈 리전 `ap-northeast-2`, Health API 클라이언트 리전 `us-east-1`, boto3 retry mode `adaptive`.
- 시간대: 날짜 계산은 모두 `Asia/Seoul` 기준 `date`.
- 추적 단위 `trackingId = {groupId}#{accountId}`, `groupId = sha256(correlationKey)[:16]`, `correlationKey = eventTypeCode#eventRegion#startTime(YYYY-MM-DD|NONE)`.
- 기본 SLA: `issue` 3일, `SECURITY` persona 7일, 그 외 30일, 여러 조건에 해당하면 최솟값.
- ACTION_REQUIRED는 어떤 설정으로도 제외되지 않는다(`suppress_event_types`는 Tier 2에만 적용).
- 수동 완료 불일치 검증 유예: 72시간.
- Slack 메시지: 계정 줄 최대 40개, 초과 시 38개 + "외 N개 계정". 메시지당 블록 ≤ 50.
- Slack 요청은 서명(v0 HMAC-SHA256, ±300초) 검증 실패 시 401 (fail-closed).
- AWS 쓰기 대상은 자체 DynamoDB/SQS뿐이다. Health/Organizations는 읽기 전용.
- `terraform apply`는 dev 환경에서만, 사용자 명시적 승인 후 실행한다. 이 Plan의 검증은 `terraform validate`/`plan`까지다.
- 포괄적 언어 규칙: allowlist/denylist, primary/replica 사용.

---

## File Structure

```
pyproject.toml                         # 패키지/의존성/pytest 설정
.gitignore
README.md                              # 셋업·배포·섀도 운영 런북
slack/manifest.yaml                    # Slack App manifest
scripts/build_lambda.sh                # Lambda zip 빌드
src/aha/
  config.py                            # Config 데이터클래스, YAML/SSM 로드
  routing.py                           # Router Protocol, StaticRouter, items_for_channel
  reconcile.py                         # Reconciler (event/entity/full sweep)
  notifier.py                          # Notifier (Intent → Slack 게시/수정)
  domain/
    models.py                          # Enum, Entity, EventSnapshot, EventRecord, EventGroup, TrackingItem, Notice, AuditEntry, Intent
    classify.py                        # Tier 분류 + Fallback
    due_date.py                        # Due Date, 우선순위, D-label
    grouping.py                        # correlationKey, groupId
    state_machine.py                   # 상태 전이, 불일치 판정
    repository.py                      # Repository Protocol, ConcurrentModification
    observe.py                         # observe(), observe_with_retry()
    actions.py                         # apply_action(), apply_group_action()
  adapters/
    repo_dynamodb.py                   # DynamoRepository
    health_client.py                   # HealthClient, to_snapshot()
    intent_queue.py                    # SqsIntentPublisher
    slack_client.py                    # SlackGateway, SlackRetryable, SlackChannelError
  slack/
    blocks.py                          # render_notice(), render_thread(), status_text(), account_label()
    views.py                           # due_date_view(), na_view(), resources_view()
    signature.py                       # verify_signature()
    interactions.py                    # InteractionService
  handlers/
    deps.py                            # 환경변수 기반 의존성 생성(lazy)
    reconcile.py                       # Lambda: 스케줄 → Reconciler
    notifier.py                        # Lambda: SQS FIFO → Notifier
    interaction.py                     # Lambda: API GW → InteractionService
tests/
  __init__.py
  conftest.py                          # AWS 테스트 자격 증명 env
  factories.py                         # NOW/TODAY/ARN, make_config/make_snapshot/make_item
  fakes.py                             # InMemoryRepository, FakeSlack, RecordingPublisher, FakeHealth, MapRouter
  dynamo_util.py                       # moto 테이블 생성
  test_*.py
infra/
  modules/core/{versions,variables,main,iam,lambda,api,alarms,outputs}.tf
  envs/dev/{main.tf,variables.tf,outputs.tf,config.yaml}
```

---

### Task 1: 프로젝트 골격, 설정, 도메인 모델

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/aha/__init__.py`, `src/aha/domain/__init__.py`, `src/aha/adapters/__init__.py`, `src/aha/slack/__init__.py`, `src/aha/handlers/__init__.py`
- Create: `src/aha/config.py`, `src/aha/domain/models.py`
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/factories.py`
- Test: `tests/test_config.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `Config(default_channel, timezone, shadow_channel, escalation_channel, sla_days, force_track_event_types, suppress_event_types, health_dashboard_url)`, `Config.from_dict(dict)`, `Config.from_yaml(str)`, `load_config(ssm_client, name) -> Config`
- Produces: `Tier`, `State`, `TERMINAL_STATES`, `DueDateSource`, `IntentKind`, `DYNAMIC_STATUSES`, `Entity(value, status)`, `EntityCounts(pending, resolved, total)`, `EntityCounts.of(entities)`, `EventSnapshot`, `EventRecord` (+`to_snapshot(account_id, entities, source)`), `EventGroup(group_id, correlation_key, primary_arn, event_arns)`, `TrackingItem` (+`tracking_id`, `is_terminal`), `tracking_id_of(group_id, account_id)`, `split_tracking_id(tid)`, `Notice(group_id, channel_id, ts, posted_channel_id)`, `AuditEntry(at, actor, action, before, after, detail=None)`, `Intent(kind, group_id, tracking_id=None, data={})` (+`to_json()`, `from_json()`)
- Produces (tests): `tests.factories.NOW`, `TODAY`, `ARN`, `ACCOUNT`, `make_config(**kw)`, `make_snapshot(**kw)`, `make_item(**kw)`

- [ ] **Step 1: git 저장소 초기화 및 기존 문서 커밋**

```bash
cd /Users/usheep/git/aws-org-health-aware
git init
git add docs/
git commit -m "docs: add Health Action Required Slack tracker spec and P1 plan"
```

- [ ] **Step 2: 패키지 설정 파일 작성**

`pyproject.toml`:
```toml
[project]
name = "aha"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "boto3>=1.35",
  "aws-lambda-powertools>=3.0",
  "slack_sdk>=3.33",
  "PyYAML>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "moto[dynamodb,sqs,ssm]>=5"]

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src", "."]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
build/
*.egg-info/
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.*
```

빈 파일 생성:
```bash
mkdir -p src/aha/domain src/aha/adapters src/aha/slack src/aha/handlers tests
touch src/aha/__init__.py src/aha/domain/__init__.py src/aha/adapters/__init__.py src/aha/slack/__init__.py src/aha/handlers/__init__.py tests/__init__.py
python3.12 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

`tests/conftest.py`:
```python
import pytest


@pytest.fixture(autouse=True)
def aws_test_env(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "ap-northeast-2",
    }.items():
        monkeypatch.setenv(key, value)
```

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/test_config.py`:
```python
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
```

`tests/test_models.py`:
```python
from aha.domain.models import (
    Entity,
    EntityCounts,
    Intent,
    IntentKind,
    State,
    split_tracking_id,
    tracking_id_of,
)
from tests.factories import make_item


def test_entity_counts():
    counts = EntityCounts.of([Entity("a", "PENDING"), Entity("b", "RESOLVED"), Entity("c", "UNKNOWN")])
    assert counts == EntityCounts(pending=1, resolved=1, total=3)


def test_tracking_id_roundtrip():
    tid = tracking_id_of("abcd", "111122223333")
    assert tid == "abcd#111122223333"
    assert split_tracking_id(tid) == ("abcd", "111122223333")


def test_tracking_item_terminal():
    assert not make_item(state=State.OPEN).is_terminal
    assert make_item(state=State.RESOLVED_AUTO).is_terminal


def test_intent_json_roundtrip():
    intent = Intent(IntentKind.DUE_CHANGED, "g1", "g1#111122223333", {"old": "2026-11-30", "new": "2027-02-28"})
    assert Intent.from_json(intent.to_json()) == intent
```

`tests/factories.py`:
```python
from datetime import UTC, date, datetime

from aha.config import Config
from aha.domain.models import DueDateSource, Entity, EventSnapshot, State, Tier, TrackingItem

NOW = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)  # 2026-10-12 09:00 KST
TODAY = date(2026, 10, 12)
ARN = "arn:aws:health:ap-northeast-2::event/RDS/AWS_RDS_PLANNED_LIFECYCLE_EVENT/abc123"
ACCOUNT = "111122223333"
OTHER_ACCOUNT = "444455556666"


def make_config(**overrides) -> Config:
    return Config.from_dict({"default_channel": "C0DEFAULT", **overrides})


def make_snapshot(**overrides) -> EventSnapshot:
    base = dict(
        event_arn=ARN,
        account_id=ACCOUNT,
        service="RDS",
        event_type_code="AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        category="scheduledChange",
        event_region="ap-northeast-2",
        status_code="upcoming",
        start_time=datetime(2026, 11, 30, 0, 0, tzinfo=UTC),
        end_time=None,
        last_updated_time=datetime(2026, 10, 1, tzinfo=UTC),
        actionability="ACTION_REQUIRED",
        personas=("OPERATIONS",),
        description="Amazon RDS for MySQL 5.7 end of standard support",
        entities=(Entity("db-1", "PENDING"), Entity("db-2", "PENDING")),
        source="RECONCILE",
    )
    base.update(overrides)
    return EventSnapshot(**base)


def make_item(**overrides) -> TrackingItem:
    base = dict(
        group_id="g1",
        account_id=ACCOUNT,
        tier=Tier.REQUIRED,
        state=State.OPEN,
        due_date=date(2026, 11, 30),
        due_date_source=DueDateSource.AWS,
        created_at=NOW,
    )
    base.update(overrides)
    return TrackingItem(**base)
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `pytest tests/test_config.py tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.config'`

- [ ] **Step 5: 구현**

`src/aha/config.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

DEFAULT_SLA_DAYS = {"issue": 3, "SECURITY": 7, "default": 30}
DEFAULT_DASHBOARD_URL = "https://health.aws.amazon.com/health/home"


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
        )

    @classmethod
    def from_yaml(cls, text: str) -> Config:
        return cls.from_dict(yaml.safe_load(text) or {})


def load_config(ssm_client, name: str) -> Config:
    return Config.from_yaml(ssm_client.get_parameter(Name=name)["Parameter"]["Value"])
```

`src/aha/domain/models.py`:
```python
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Tier(StrEnum):
    REQUIRED = "TIER1"
    REVIEW = "TIER2"
    EXCLUDED = "EXCLUDED"


class State(StrEnum):
    TRIAGE = "TRIAGE"
    OPEN = "OPEN"
    RESOLVED_AUTO = "RESOLVED_AUTO"
    RESOLVED_MANUAL = "RESOLVED_MANUAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CLOSED_BY_AWS = "CLOSED_BY_AWS"


TERMINAL_STATES = frozenset(
    {State.RESOLVED_AUTO, State.RESOLVED_MANUAL, State.NOT_APPLICABLE, State.CLOSED_BY_AWS}
)


class DueDateSource(StrEnum):
    AWS = "AWS"
    DEFAULT_SLA = "DEFAULT_SLA"
    MANUAL = "MANUAL"


class IntentKind(StrEnum):
    NEW = "NEW"
    CONTENT_UPDATED = "CONTENT_UPDATED"
    DUE_CHANGED = "DUE_CHANGED"
    PROGRESS = "PROGRESS"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    CLOSED_BY_AWS = "CLOSED_BY_AWS"
    MISMATCH = "MISMATCH"
    PROMOTED = "PROMOTED"
    ACTION = "ACTION"
    REFRESH = "REFRESH"


# 리소스 상태 중 이 값이 하나라도 있으면 해당 이벤트는 동적 상태 업데이트를 지원한다.
DYNAMIC_STATUSES = frozenset({"PENDING", "RESOLVED"})


@dataclass(frozen=True)
class Entity:
    value: str
    status: str  # IMPAIRED | UNIMPAIRED | UNKNOWN | PENDING | RESOLVED


@dataclass(frozen=True)
class EntityCounts:
    pending: int = 0
    resolved: int = 0
    total: int = 0

    @classmethod
    def of(cls, entities: Iterable[Entity]) -> EntityCounts:
        items = list(entities)
        return cls(
            pending=sum(e.status == "PENDING" for e in items),
            resolved=sum(e.status == "RESOLVED" for e in items),
            total=len(items),
        )


@dataclass(frozen=True)
class EventSnapshot:
    event_arn: str
    account_id: str
    service: str
    event_type_code: str
    category: str
    event_region: str
    status_code: str
    start_time: datetime | None
    end_time: datetime | None
    last_updated_time: datetime
    actionability: str | None
    personas: tuple[str, ...]
    description: str | None
    entities: tuple[Entity, ...]
    source: str


@dataclass
class EventRecord:
    event_arn: str
    group_id: str
    service: str
    event_type_code: str
    category: str
    event_region: str
    status_code: str
    start_time: datetime | None
    end_time: datetime | None
    last_updated_time: datetime
    actionability: str | None
    personas: tuple[str, ...]
    description: str | None
    description_hash: str | None
    dynamic_status: bool

    def to_snapshot(self, account_id: str, entities: Iterable[Entity], source: str) -> EventSnapshot:
        return EventSnapshot(
            event_arn=self.event_arn,
            account_id=account_id,
            service=self.service,
            event_type_code=self.event_type_code,
            category=self.category,
            event_region=self.event_region,
            status_code=self.status_code,
            start_time=self.start_time,
            end_time=self.end_time,
            last_updated_time=self.last_updated_time,
            actionability=self.actionability,
            personas=self.personas,
            description=None,
            entities=tuple(entities),
            source=source,
        )


@dataclass
class EventGroup:
    group_id: str
    correlation_key: str
    primary_arn: str
    event_arns: set[str] = field(default_factory=set)


def tracking_id_of(group_id: str, account_id: str) -> str:
    return f"{group_id}#{account_id}"


def split_tracking_id(tracking_id: str) -> tuple[str, str]:
    group_id, account_id = tracking_id.split("#", 1)
    return group_id, account_id


@dataclass
class TrackingItem:
    group_id: str
    account_id: str
    tier: Tier
    state: State
    due_date: date
    due_date_source: DueDateSource
    created_at: datetime
    event_arns: set[str] = field(default_factory=set)
    aws_due_date: date | None = None
    assignee: str | None = None
    reminders_sent: set[str] = field(default_factory=set)
    manual_completion_required: bool = False
    verification_mismatch: bool = False
    counts: EntityCounts = field(default_factory=EntityCounts)
    resolved_at: datetime | None = None
    manual_resolved_at: datetime | None = None
    version: int = 0

    @property
    def tracking_id(self) -> str:
        return tracking_id_of(self.group_id, self.account_id)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass
class Notice:
    group_id: str
    channel_id: str  # 라우팅상 채널
    ts: str
    posted_channel_id: str  # 실제 게시 채널 (대체 게시 시 default_channel)


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    action: str
    before: str | None
    after: str | None
    detail: str | None = None


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    group_id: str
    tracking_id: str | None = None
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"kind": self.kind.value, "group_id": self.group_id, "tracking_id": self.tracking_id, "data": self.data},
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, body: str) -> Intent:
        raw = json.loads(body)
        return cls(IntentKind(raw["kind"]), raw["group_id"], raw.get("tracking_id"), raw.get("data") or {})
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_config.py tests/test_models.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "feat: add project skeleton, config loader and domain models"
```

---

### Task 2: 이벤트 분류 (Tier + Fallback)

**Files:**
- Create: `src/aha/domain/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `Config`, `EventSnapshot`, `Tier`
- Produces: `classify(snap: EventSnapshot, cfg: Config) -> tuple[Tier, str]` — 두 번째 값은 `"FORCED" | "ACTIONABILITY" | "FALLBACK" | "SUPPRESSED"`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_classify.py`:
```python
import pytest

from aha.domain.classify import classify
from aha.domain.models import Entity, Tier
from tests.factories import make_config, make_snapshot

CFG = make_config()


@pytest.mark.parametrize(
    ("actionability", "expected"),
    [
        ("ACTION_REQUIRED", Tier.REQUIRED),
        ("ACTION_MAY_BE_REQUIRED", Tier.REVIEW),
        ("INFORMATIONAL", Tier.EXCLUDED),
    ],
)
def test_actionability_is_primary_signal(actionability, expected):
    assert classify(make_snapshot(actionability=actionability), CFG) == (expected, "ACTIONABILITY")


def test_suppress_list_applies_only_to_review_tier():
    cfg = make_config(suppress_event_types=["AWS_RDS_PLANNED_LIFECYCLE_EVENT"])
    assert classify(make_snapshot(actionability="ACTION_MAY_BE_REQUIRED"), cfg) == (Tier.EXCLUDED, "SUPPRESSED")
    assert classify(make_snapshot(actionability="ACTION_REQUIRED"), cfg) == (Tier.REQUIRED, "ACTIONABILITY")


def test_force_list_overrides_informational():
    cfg = make_config(force_track_event_types=["AWS_RDS_PLANNED_LIFECYCLE_EVENT"])
    assert classify(make_snapshot(actionability="INFORMATIONAL"), cfg) == (Tier.REQUIRED, "FORCED")


@pytest.mark.parametrize(
    ("category", "code", "entities", "expected"),
    [
        ("scheduledChange", "AWS_EC2_MAINTENANCE", (), Tier.REQUIRED),
        ("issue", "AWS_EC2_INSTANCE_RETIREMENT_SCHEDULED", (), Tier.REQUIRED),
        ("accountNotification", "AWS_RDS_PLANNED_LIFECYCLE_EVENT", (), Tier.REQUIRED),
        ("accountNotification", "AWS_IAM_NOTICE", (), Tier.REVIEW),
        ("issue", "AWS_EC2_OPERATIONAL_ISSUE", (Entity("i-1", "IMPAIRED"),), Tier.REVIEW),
        ("issue", "AWS_EC2_OPERATIONAL_ISSUE", (), Tier.EXCLUDED),
        ("investigation", "AWS_EC2_INVESTIGATION", (), Tier.EXCLUDED),
    ],
)
def test_fallback_when_actionability_missing(category, code, entities, expected):
    snap = make_snapshot(actionability=None, category=category, event_type_code=code, entities=entities)
    assert classify(snap, CFG) == (expected, "FALLBACK")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.domain.classify'`

- [ ] **Step 3: 구현**

`src/aha/domain/classify.py`:
```python
from __future__ import annotations

from aha.config import Config
from aha.domain.models import EventSnapshot, Tier

FALLBACK_TIER1_MARKERS = ("PLANNED_LIFECYCLE_EVENT", "_SCHEDULED", "RETIREMENT")


def classify(snap: EventSnapshot, cfg: Config) -> tuple[Tier, str]:
    if snap.event_type_code in cfg.force_track_event_types:
        return Tier.REQUIRED, "FORCED"
    if snap.actionability == "ACTION_REQUIRED":
        return Tier.REQUIRED, "ACTIONABILITY"
    if snap.actionability == "INFORMATIONAL":
        return Tier.EXCLUDED, "ACTIONABILITY"
    if snap.actionability == "ACTION_MAY_BE_REQUIRED":
        tier, source = Tier.REVIEW, "ACTIONABILITY"
    else:
        tier, source = _fallback(snap), "FALLBACK"
    if tier is Tier.REVIEW and snap.event_type_code in cfg.suppress_event_types:
        return Tier.EXCLUDED, "SUPPRESSED"
    return tier, source


def _fallback(snap: EventSnapshot) -> Tier:
    if snap.category == "scheduledChange" or any(m in snap.event_type_code for m in FALLBACK_TIER1_MARKERS):
        return Tier.REQUIRED
    if snap.category == "accountNotification":
        return Tier.REVIEW
    if snap.category == "issue" and snap.entities:
        return Tier.REVIEW
    return Tier.EXCLUDED
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_classify.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/domain/classify.py tests/test_classify.py
git commit -m "feat: classify Health events into tracking tiers"
```

---

### Task 3: Due Date, 우선순위, 그룹 키

**Files:**
- Create: `src/aha/domain/due_date.py`, `src/aha/domain/grouping.py`
- Test: `tests/test_due_date.py`, `tests/test_grouping.py`

**Interfaces:**
- Consumes: `Config`, `EventSnapshot`, `DueDateSource`
- Produces: `to_local_date(ts: datetime, tz: str) -> date`, `compute_due(snap, first_seen: date, cfg) -> tuple[date, DueDateSource]`, `sla_days(snap, cfg) -> int`, `priority(personas: tuple[str, ...], due: date, today: date) -> str` (`"P1"|"P2"|"P3"`), `d_label(due, today) -> str`
- Produces: `correlation_key(snap, tz: str) -> str`, `group_id_for(key: str) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_due_date.py`:
```python
from datetime import UTC, date, datetime

import pytest

from aha.domain.due_date import compute_due, d_label, priority, to_local_date
from aha.domain.models import DueDateSource
from tests.factories import TODAY, make_config, make_snapshot

CFG = make_config()


def test_to_local_date_uses_kst():
    assert to_local_date(datetime(2026, 11, 30, 16, 0, tzinfo=UTC), "Asia/Seoul") == date(2026, 12, 1)


def test_lifecycle_uses_start_time():
    assert compute_due(make_snapshot(), TODAY, CFG) == (date(2026, 11, 30), DueDateSource.AWS)


def test_lifecycle_falls_back_to_end_time():
    snap = make_snapshot(start_time=None, end_time=datetime(2027, 1, 15, tzinfo=UTC))
    assert compute_due(snap, TODAY, CFG) == (date(2027, 1, 15), DueDateSource.AWS)


def test_lifecycle_without_dates_uses_default_sla():
    snap = make_snapshot(start_time=None, end_time=None)
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 11), DueDateSource.DEFAULT_SLA)


def test_notification_with_future_start_uses_start():
    snap = make_snapshot(category="accountNotification", event_type_code="AWS_IAM_NOTICE")
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 30), DueDateSource.AWS)


def test_notification_with_past_start_uses_sla():
    snap = make_snapshot(
        category="accountNotification",
        event_type_code="AWS_IAM_NOTICE",
        start_time=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert compute_due(snap, TODAY, CFG) == (date(2026, 11, 11), DueDateSource.DEFAULT_SLA)


@pytest.mark.parametrize(
    ("category", "personas", "expected"),
    [
        ("issue", ("OPERATIONS",), date(2026, 10, 15)),
        ("accountNotification", ("SECURITY",), date(2026, 10, 19)),
        ("issue", ("SECURITY",), date(2026, 10, 15)),
    ],
)
def test_sla_takes_shortest_matching_rule(category, personas, expected):
    snap = make_snapshot(category=category, event_type_code="AWS_X_NOTICE", start_time=None, personas=personas)
    assert compute_due(snap, TODAY, CFG) == (expected, DueDateSource.DEFAULT_SLA)


@pytest.mark.parametrize(
    ("personas", "due", "expected"),
    [
        (("SECURITY",), date(2027, 6, 1), "P1"),
        (("OPERATIONS",), date(2026, 10, 26), "P1"),
        (("OPERATIONS",), date(2026, 11, 11), "P2"),
        (("OPERATIONS",), date(2026, 11, 12), "P3"),
    ],
)
def test_priority(personas, due, expected):
    assert priority(personas, due, TODAY) == expected


@pytest.mark.parametrize(
    ("due", "expected"),
    [(date(2026, 11, 30), "D-49"), (TODAY, "D-day"), (date(2026, 10, 9), "D+3")],
)
def test_d_label(due, expected):
    assert d_label(due, TODAY) == expected
```

`tests/test_grouping.py`:
```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_due_date.py tests/test_grouping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.domain.due_date'`

- [ ] **Step 3: 구현**

`src/aha/domain/due_date.py`:
```python
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aha.config import Config
from aha.domain.models import DueDateSource, EventSnapshot


def to_local_date(ts: datetime, tz: str) -> date:
    return ts.astimezone(ZoneInfo(tz)).date()


def _is_lifecycle(snap: EventSnapshot) -> bool:
    return snap.category == "scheduledChange" or "PLANNED_LIFECYCLE_EVENT" in snap.event_type_code


def sla_days(snap: EventSnapshot, cfg: Config) -> int:
    candidates = [cfg.sla_days["default"]]
    if snap.category == "issue":
        candidates.append(cfg.sla_days["issue"])
    if "SECURITY" in snap.personas:
        candidates.append(cfg.sla_days["SECURITY"])
    return min(candidates)


def compute_due(snap: EventSnapshot, first_seen: date, cfg: Config) -> tuple[date, DueDateSource]:
    if _is_lifecycle(snap):
        ts = snap.start_time or snap.end_time
        if ts is not None:
            return to_local_date(ts, cfg.timezone), DueDateSource.AWS
    elif snap.start_time is not None:
        start = to_local_date(snap.start_time, cfg.timezone)
        if start > first_seen:
            return start, DueDateSource.AWS
    return first_seen + timedelta(days=sla_days(snap, cfg)), DueDateSource.DEFAULT_SLA


def priority(personas: tuple[str, ...], due: date, today: date) -> str:
    days = (due - today).days
    if "SECURITY" in personas or days <= 14:
        return "P1"
    if days <= 30:
        return "P2"
    return "P3"


def d_label(due: date, today: date) -> str:
    days = (due - today).days
    if days == 0:
        return "D-day"
    return f"D-{days}" if days > 0 else f"D+{-days}"
```

`src/aha/domain/grouping.py`:
```python
from __future__ import annotations

import hashlib

from aha.domain.due_date import to_local_date
from aha.domain.models import EventSnapshot


def correlation_key(snap: EventSnapshot, tz: str) -> str:
    start = to_local_date(snap.start_time, tz).isoformat() if snap.start_time else "NONE"
    return f"{snap.event_type_code}#{snap.event_region}#{start}"


def group_id_for(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_due_date.py tests/test_grouping.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/domain/due_date.py src/aha/domain/grouping.py tests/test_due_date.py tests/test_grouping.py
git commit -m "feat: compute due dates, priority and event group keys"
```

---

### Task 4: 상태 전이 규칙

**Files:**
- Create: `src/aha/domain/state_machine.py`
- Test: `tests/test_state_machine.py`

**Interfaces:**
- Consumes: `TrackingItem`, `EntityCounts`, `State`, `Tier`
- Produces: `next_state(item, counts, dynamic: bool, status_code: str) -> State`, `needs_mismatch_flag(item, counts, now: datetime) -> bool`, `MISMATCH_GRACE = timedelta(hours=72)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_state_machine.py`:
```python
from datetime import timedelta

import pytest

from aha.domain.models import EntityCounts, State, Tier
from aha.domain.state_machine import needs_mismatch_flag, next_state
from tests.factories import NOW, make_item

ALL_RESOLVED = EntityCounts(pending=0, resolved=2, total=2)
ONE_PENDING = EntityCounts(pending=1, resolved=1, total=2)


@pytest.mark.parametrize("state", [State.OPEN, State.TRIAGE])
def test_active_item_auto_resolves_when_all_entities_resolved(state):
    assert next_state(make_item(state=state), ALL_RESOLVED, True, "open") is State.RESOLVED_AUTO


def test_no_auto_resolve_without_entities():
    assert next_state(make_item(), EntityCounts(), True, "open") is State.OPEN


def test_closed_event_without_dynamic_status_closes_by_aws():
    assert next_state(make_item(), EntityCounts(total=1), False, "closed") is State.CLOSED_BY_AWS


def test_closed_dynamic_event_with_pending_stays_open():
    assert next_state(make_item(), ONE_PENDING, True, "closed") is State.OPEN


def test_resolved_auto_reopens_on_new_pending():
    assert next_state(make_item(state=State.RESOLVED_AUTO), ONE_PENDING, True, "open") is State.OPEN
    review = make_item(state=State.RESOLVED_AUTO, tier=Tier.REVIEW)
    assert next_state(review, ONE_PENDING, True, "open") is State.TRIAGE


@pytest.mark.parametrize("state", [State.RESOLVED_MANUAL, State.NOT_APPLICABLE, State.CLOSED_BY_AWS])
def test_other_terminal_states_are_sticky(state):
    assert next_state(make_item(state=state), ONE_PENDING, True, "open") is state


def test_mismatch_flag_after_grace_period():
    item = make_item(state=State.RESOLVED_MANUAL, manual_resolved_at=NOW)
    assert not needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=71))
    assert needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=72))
    assert not needs_mismatch_flag(item, ALL_RESOLVED, NOW + timedelta(hours=72))
    item.verification_mismatch = True
    assert not needs_mismatch_flag(item, ONE_PENDING, NOW + timedelta(hours=72))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_state_machine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.domain.state_machine'`

- [ ] **Step 3: 구현**

`src/aha/domain/state_machine.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta

from aha.domain.models import EntityCounts, State, Tier, TrackingItem

# AWS 리소스 상태 반영은 최대 72시간 지연될 수 있다.
MISMATCH_GRACE = timedelta(hours=72)
ACTIVE_STATES = (State.TRIAGE, State.OPEN)


def next_state(item: TrackingItem, counts: EntityCounts, dynamic: bool, status_code: str) -> State:
    if item.state in ACTIVE_STATES:
        if dynamic and counts.total > 0 and counts.resolved == counts.total:
            return State.RESOLVED_AUTO
        if status_code == "closed" and not dynamic:
            return State.CLOSED_BY_AWS
        return item.state
    if item.state is State.RESOLVED_AUTO and counts.pending > 0:
        return State.OPEN if item.tier is Tier.REQUIRED else State.TRIAGE
    return item.state


def needs_mismatch_flag(item: TrackingItem, counts: EntityCounts, now: datetime) -> bool:
    return (
        item.state is State.RESOLVED_MANUAL
        and not item.verification_mismatch
        and item.manual_resolved_at is not None
        and now - item.manual_resolved_at >= MISMATCH_GRACE
        and counts.pending > 0
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_state_machine.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/domain/state_machine.py tests/test_state_machine.py
git commit -m "feat: add tracking item state transition rules"
```

---

### Task 5: Repository 계약, In-memory Fake, `observe()`

**Files:**
- Create: `src/aha/domain/repository.py`, `src/aha/domain/observe.py`
- Create: `tests/fakes.py`
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: Task 1–4의 모든 도메인 함수
- Produces: `ConcurrentModification(Exception)`, `Repository` Protocol:
  - `get_event(arn) -> EventRecord | None`, `put_event(record)`
  - `get_group(group_id) -> EventGroup | None`, `put_group(group)` (event_arns 병합)
  - `get_tracking(tid) -> TrackingItem | None`, `create_tracking(item) -> bool` (성공 시 `item.version = 1`), `save_tracking(item)` (`item.version` 불일치 시 `ConcurrentModification`, 성공 시 +1)
  - `list_group_tracking(group_id) -> list[TrackingItem]` (account_id 순), `list_by_state(state) -> list[TrackingItem]` (due_date, tracking_id 순)
  - `upsert_entities(tid, entities, now)`, `list_entities(tid) -> list[Entity]` (value 순)
  - `append_audit(tid, entry)`, `list_audit(tid) -> list[AuditEntry]`
  - `get_notices(group_id) -> list[Notice]`, `put_notice(notice)`
  - `get_cursor(name) -> datetime | None`, `set_cursor(name, value)`
- Produces: `observe(snap, repo, cfg, now) -> list[Intent]`, `observe_with_retry(snap, repo, cfg, now, attempts=3) -> list[Intent]`
- Produces (tests): `tests.fakes.InMemoryRepository`

- [ ] **Step 1: Repository Protocol 및 In-memory Fake 작성**

`src/aha/domain/repository.py`:
```python
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from aha.domain.models import AuditEntry, Entity, EventGroup, EventRecord, Notice, State, TrackingItem


class ConcurrentModification(Exception):
    """TrackingItem 낙관적 잠금 충돌."""


class Repository(Protocol):
    def get_event(self, event_arn: str) -> EventRecord | None: ...
    def put_event(self, record: EventRecord) -> None: ...
    def get_group(self, group_id: str) -> EventGroup | None: ...
    def put_group(self, group: EventGroup) -> None: ...
    def get_tracking(self, tracking_id: str) -> TrackingItem | None: ...
    def create_tracking(self, item: TrackingItem) -> bool: ...
    def save_tracking(self, item: TrackingItem) -> None: ...
    def list_group_tracking(self, group_id: str) -> list[TrackingItem]: ...
    def list_by_state(self, state: State) -> list[TrackingItem]: ...
    def upsert_entities(self, tracking_id: str, entities: Iterable[Entity], now: datetime) -> None: ...
    def list_entities(self, tracking_id: str) -> list[Entity]: ...
    def append_audit(self, tracking_id: str, entry: AuditEntry) -> None: ...
    def list_audit(self, tracking_id: str) -> list[AuditEntry]: ...
    def get_notices(self, group_id: str) -> list[Notice]: ...
    def put_notice(self, notice: Notice) -> None: ...
    def get_cursor(self, name: str) -> datetime | None: ...
    def set_cursor(self, name: str, value: datetime) -> None: ...
```

`tests/fakes.py`:
```python
from __future__ import annotations

import copy
from collections import defaultdict

from aha.domain.repository import ConcurrentModification


class InMemoryRepository:
    def __init__(self):
        self.events = {}
        self.groups = {}
        self.tracking = {}
        self.entities = defaultdict(dict)
        self.audit = defaultdict(list)
        self.notices = {}
        self.cursors = {}

    def get_event(self, event_arn):
        return copy.deepcopy(self.events.get(event_arn))

    def put_event(self, record):
        self.events[record.event_arn] = copy.deepcopy(record)

    def get_group(self, group_id):
        return copy.deepcopy(self.groups.get(group_id))

    def put_group(self, group):
        existing = self.groups.get(group.group_id)
        if existing is None:
            self.groups[group.group_id] = copy.deepcopy(group)
        else:
            existing.event_arns |= group.event_arns

    def get_tracking(self, tracking_id):
        return copy.deepcopy(self.tracking.get(tracking_id))

    def create_tracking(self, item):
        if item.tracking_id in self.tracking:
            return False
        item.version = 1
        self.tracking[item.tracking_id] = copy.deepcopy(item)
        return True

    def save_tracking(self, item):
        stored = self.tracking.get(item.tracking_id)
        if stored is None or stored.version != item.version:
            raise ConcurrentModification(item.tracking_id)
        item.version += 1
        self.tracking[item.tracking_id] = copy.deepcopy(item)

    def list_group_tracking(self, group_id):
        items = [t for t in self.tracking.values() if t.group_id == group_id]
        return [copy.deepcopy(t) for t in sorted(items, key=lambda t: t.account_id)]

    def list_by_state(self, state):
        items = [t for t in self.tracking.values() if t.state is state]
        return [copy.deepcopy(t) for t in sorted(items, key=lambda t: (t.due_date, t.tracking_id))]

    def upsert_entities(self, tracking_id, entities, now):
        for entity in entities:
            self.entities[tracking_id][entity.value] = entity

    def list_entities(self, tracking_id):
        return [self.entities[tracking_id][k] for k in sorted(self.entities[tracking_id])]

    def append_audit(self, tracking_id, entry):
        self.audit[tracking_id].append(entry)

    def list_audit(self, tracking_id):
        return list(self.audit[tracking_id])

    def get_notices(self, group_id):
        return [copy.deepcopy(n) for (g, _), n in sorted(self.notices.items()) if g == group_id]

    def put_notice(self, notice):
        self.notices[(notice.group_id, notice.channel_id)] = copy.deepcopy(notice)

    def get_cursor(self, name):
        return self.cursors.get(name)

    def set_cursor(self, name, value):
        self.cursors[name] = value
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_observe.py`:
```python
from datetime import UTC, date, datetime, timedelta

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Entity, IntentKind, State, Tier, tracking_id_of
from aha.domain.observe import observe, observe_with_retry
from aha.domain.repository import ConcurrentModification
from tests.factories import ACCOUNT, ARN, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import InMemoryRepository

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
LATER = datetime(2026, 10, 5, tzinfo=UTC)
RESOLVED_BOTH = (Entity("db-1", "RESOLVED"), Entity("db-2", "RESOLVED"))


def _kinds(intents):
    return [i.kind for i in intents]


def _initial(repo, **overrides):
    return observe(make_snapshot(**overrides), repo, CFG, NOW)


# --- 생성 경로 ---

def test_action_required_creates_open_item_and_new_intent():
    repo = InMemoryRepository()
    intents = _initial(repo)
    assert _kinds(intents) == [IntentKind.NEW]
    assert intents[0].tracking_id == TID
    item = repo.get_tracking(TID)
    assert item.state is State.OPEN
    assert item.tier is Tier.REQUIRED
    assert (item.due_date, item.due_date_source) == (date(2026, 11, 30), DueDateSource.AWS)
    assert item.aws_due_date == date(2026, 11, 30)
    assert item.counts.pending == 2
    assert item.manual_completion_required is False
    assert repo.list_audit(TID)[0].action == "CREATED"


def test_action_may_be_required_creates_triage_item():
    repo = InMemoryRepository()
    _initial(repo, actionability="ACTION_MAY_BE_REQUIRED")
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.TRIAGE, Tier.REVIEW)


def test_informational_is_ignored():
    repo = InMemoryRepository()
    assert _initial(repo, actionability="INFORMATIONAL") == []
    assert repo.tracking == {}


def test_event_without_dynamic_status_requires_manual_completion():
    repo = InMemoryRepository()
    _initial(repo, entities=(Entity("i-1", "UNKNOWN"),))
    assert repo.get_tracking(TID).manual_completion_required is True


def test_already_resolved_on_discovery_is_stored_silently():
    repo = InMemoryRepository()
    assert _initial(repo, entities=RESOLVED_BOTH) == []
    assert repo.get_tracking(TID).state is State.RESOLVED_AUTO


def test_reobserving_same_snapshot_is_silent():
    repo = InMemoryRepository()
    _initial(repo)
    version = repo.get_tracking(TID).version
    assert _initial(repo) == []
    assert repo.get_tracking(TID).version == version


def test_second_account_joins_same_group():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, account_id=OTHER_ACCOUNT)
    assert _kinds(intents) == [IntentKind.NEW]
    assert [i.account_id for i in repo.list_group_tracking(GID)] == [ACCOUNT, OTHER_ACCOUNT]


def test_split_event_arn_links_to_existing_item():
    repo = InMemoryRepository()
    _initial(repo)
    part2 = ARN + "-part2"
    intents = _initial(repo, event_arn=part2, entities=(Entity("db-3", "PENDING"),))
    assert _kinds(intents) == [IntentKind.PROGRESS]
    assert repo.get_tracking(TID).event_arns == {ARN, part2}
    assert repo.get_group(GID).event_arns == {ARN, part2}


# --- 갱신 경로 ---

def test_aws_due_date_change_is_applied():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    assert _kinds(intents) == [IntentKind.DUE_CHANGED]
    assert intents[0].data == {"old": "2026-11-30", "new": "2027-02-28", "source": "AWS", "kept_manual": False}
    assert repo.get_tracking(TID).due_date == date(2027, 2, 28)


def test_manual_due_date_is_kept_when_aws_changes():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.due_date, item.due_date_source = date(2026, 12, 15), DueDateSource.MANUAL
    repo.save_tracking(item)
    intents = _initial(repo, start_time=datetime(2027, 2, 28, tzinfo=UTC), last_updated_time=LATER)
    assert intents[0].data == {"old": "2026-11-30", "new": "2027-02-28", "source": "AWS", "kept_manual": True}
    item = repo.get_tracking(TID)
    assert (item.due_date, item.aws_due_date) == (date(2026, 12, 15), date(2027, 2, 28))


def test_progress_when_entity_resolved():
    repo = InMemoryRepository()
    _initial(repo)
    intents = _initial(repo, entities=(Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")))
    assert _kinds(intents) == [IntentKind.PROGRESS]
    assert intents[0].data == {"old_pending": 2, "new_pending": 1, "total": 2}


def test_all_entities_resolved_closes_item():
    repo = InMemoryRepository()
    _initial(repo)
    later = NOW + timedelta(hours=6)
    intents = observe(make_snapshot(entities=RESOLVED_BOTH), repo, CFG, later)
    assert _kinds(intents) == [IntentKind.RESOLVED]
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at) == (State.RESOLVED_AUTO, later)


def test_new_pending_entity_reopens_resolved_item():
    repo = InMemoryRepository()
    _initial(repo)
    _initial(repo, entities=RESOLVED_BOTH)
    intents = _initial(repo, entities=(Entity("db-3", "PENDING"),))
    assert _kinds(intents) == [IntentKind.REOPENED]
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at) == (State.OPEN, None)


def test_aws_closed_event_without_dynamic_status():
    repo = InMemoryRepository()
    _initial(repo, entities=(Entity("i-1", "UNKNOWN"),))
    intents = _initial(repo, entities=(Entity("i-1", "UNKNOWN"),), status_code="closed", last_updated_time=LATER)
    assert _kinds(intents) == [IntentKind.CLOSED_BY_AWS]


def test_description_change_emits_single_group_intent():
    repo = InMemoryRepository()
    _initial(repo)
    _initial(repo, account_id=OTHER_ACCOUNT)
    first = _initial(repo, description="updated", last_updated_time=LATER)
    second = _initial(repo, account_id=OTHER_ACCOUNT, description="updated", last_updated_time=LATER)
    assert _kinds(first) == [IntentKind.CONTENT_UPDATED]
    assert first[0].tracking_id is None
    assert second == []


def test_stale_snapshot_does_not_overwrite_event():
    repo = InMemoryRepository()
    _initial(repo, description="new", last_updated_time=LATER)
    _initial(repo, description="old", last_updated_time=datetime(2026, 10, 1, tzinfo=UTC))
    assert repo.get_event(ARN).description == "new"


def test_review_item_promoted_when_action_required():
    repo = InMemoryRepository()
    _initial(repo, actionability="ACTION_MAY_BE_REQUIRED")
    intents = _initial(repo)
    assert _kinds(intents) == [IntentKind.PROMOTED]
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.OPEN, Tier.REQUIRED)


def test_manual_resolution_mismatch_flagged_once_after_grace():
    repo = InMemoryRepository()
    _initial(repo)
    item = repo.get_tracking(TID)
    item.state, item.manual_resolved_at, item.resolved_at = State.RESOLVED_MANUAL, NOW, NOW
    repo.save_tracking(item)
    assert observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=71)) == []
    intents = observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=73))
    assert _kinds(intents) == [IntentKind.MISMATCH]
    assert intents[0].data == {"pending": 2}
    assert observe(make_snapshot(), repo, CFG, NOW + timedelta(hours=74)) == []


class FlakyRepository(InMemoryRepository):
    def __init__(self):
        super().__init__()
        self.fail_next_save = False

    def save_tracking(self, item):
        if self.fail_next_save:
            self.fail_next_save = False
            raise ConcurrentModification(item.tracking_id)
        super().save_tracking(item)


def test_observe_with_retry_recovers_from_conflict():
    repo = FlakyRepository()
    _initial(repo)
    repo.fail_next_save = True
    intents = observe_with_retry(make_snapshot(entities=RESOLVED_BOTH), repo, CFG, NOW)
    assert _kinds(intents) == [IntentKind.RESOLVED]
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_observe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.domain.observe'`

- [ ] **Step 4: 구현**

`src/aha/domain/observe.py`:
```python
from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from aha.config import Config
from aha.domain.classify import classify
from aha.domain.due_date import compute_due, to_local_date
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import (
    DYNAMIC_STATUSES,
    AuditEntry,
    DueDateSource,
    EntityCounts,
    EventGroup,
    EventRecord,
    EventSnapshot,
    Intent,
    IntentKind,
    State,
    Tier,
    TrackingItem,
    tracking_id_of,
)
from aha.domain.repository import ConcurrentModification, Repository
from aha.domain.state_machine import needs_mismatch_flag, next_state

STATE_CHANGE_INTENTS = {
    State.RESOLVED_AUTO: IntentKind.RESOLVED,
    State.CLOSED_BY_AWS: IntentKind.CLOSED_BY_AWS,
    State.OPEN: IntentKind.REOPENED,
    State.TRIAGE: IntentKind.REOPENED,
}


def observe(snap: EventSnapshot, repo: Repository, cfg: Config, now: datetime) -> list[Intent]:
    """스냅샷 1건(eventArn × 계정)을 상태에 반영하고, 알림이 필요한 변화만 Intent로 돌려준다."""
    tier, _ = classify(snap, cfg)
    if tier is Tier.EXCLUDED:
        return []
    record, content_changed = _upsert_event(snap, repo, cfg)
    group = _ensure_group(snap, record, repo, cfg)
    tracking_id = tracking_id_of(group.group_id, snap.account_id)
    repo.upsert_entities(tracking_id, snap.entities, now)
    counts = EntityCounts.of(repo.list_entities(tracking_id))

    intents = [Intent(IntentKind.CONTENT_UPDATED, group.group_id)] if content_changed else []
    item = repo.get_tracking(tracking_id)
    if item is None:
        intents += _create(snap, record, group, tier, counts, repo, cfg, now)
    else:
        intents += _update(item, snap, record, tier, counts, repo, cfg, now)
    return intents


def observe_with_retry(
    snap: EventSnapshot, repo: Repository, cfg: Config, now: datetime, attempts: int = 3
) -> list[Intent]:
    for attempt in range(attempts):
        try:
            return observe(snap, repo, cfg, now)
        except ConcurrentModification:
            if attempt == attempts - 1:
                raise
    return []


def _hash(text: str | None) -> str | None:
    return hashlib.sha256(text.encode()).hexdigest() if text else None


def _upsert_event(snap: EventSnapshot, repo: Repository, cfg: Config) -> tuple[EventRecord, bool]:
    prev = repo.get_event(snap.event_arn)
    dynamic = any(e.status in DYNAMIC_STATUSES for e in snap.entities)
    if prev is not None and snap.last_updated_time < prev.last_updated_time:
        if dynamic and not prev.dynamic_status:
            prev.dynamic_status = True
            repo.put_event(prev)
        return prev, False

    description = snap.description if snap.description is not None else (prev.description if prev else None)
    description_hash = _hash(description)
    changed = prev is not None and prev.description_hash is not None and description_hash != prev.description_hash
    record = EventRecord(
        event_arn=snap.event_arn,
        group_id=prev.group_id if prev else group_id_for(correlation_key(snap, cfg.timezone)),
        service=snap.service,
        event_type_code=snap.event_type_code,
        category=snap.category,
        event_region=snap.event_region,
        status_code=snap.status_code,
        start_time=snap.start_time,
        end_time=snap.end_time,
        last_updated_time=snap.last_updated_time,
        actionability=snap.actionability,
        personas=snap.personas,
        description=description,
        description_hash=description_hash,
        dynamic_status=dynamic or (prev.dynamic_status if prev else False),
    )
    if record != prev:
        repo.put_event(record)
    return record, changed


def _ensure_group(snap: EventSnapshot, record: EventRecord, repo: Repository, cfg: Config) -> EventGroup:
    group = repo.get_group(record.group_id)
    if group is None:
        group = EventGroup(record.group_id, correlation_key(snap, cfg.timezone), snap.event_arn, {snap.event_arn})
    elif snap.event_arn in group.event_arns:
        return group
    else:
        group.event_arns.add(snap.event_arn)
    repo.put_group(group)
    return group


def _create(snap, record, group, tier, counts, repo, cfg, now) -> list[Intent]:
    due, source = compute_due(snap, to_local_date(now, cfg.timezone), cfg)
    item = TrackingItem(
        group_id=group.group_id,
        account_id=snap.account_id,
        tier=tier,
        state=State.OPEN if tier is Tier.REQUIRED else State.TRIAGE,
        due_date=due,
        due_date_source=source,
        created_at=now,
        event_arns={snap.event_arn},
        aws_due_date=due if source is DueDateSource.AWS else None,
        manual_completion_required=not record.dynamic_status,
        counts=counts,
    )
    initial = next_state(item, counts, record.dynamic_status, snap.status_code)
    if initial is not item.state:
        item.state, item.resolved_at = initial, now
    if not repo.create_tracking(item):
        raise ConcurrentModification(item.tracking_id)
    repo.append_audit(item.tracking_id, AuditEntry(now, "system", "CREATED", None, item.state.value))
    # 발견 시점에 이미 종결된 항목은 알릴 필요가 없다.
    return [] if item.is_terminal else [Intent(IntentKind.NEW, group.group_id, item.tracking_id)]


def _update(item, snap, record, tier, counts, repo, cfg, now) -> list[Intent]:
    original = copy.deepcopy(item)
    intents: list[Intent] = []
    item.event_arns.add(snap.event_arn)
    item.manual_completion_required = not record.dynamic_status

    if tier is Tier.REQUIRED and item.tier is Tier.REVIEW:
        item.tier = Tier.REQUIRED
        if item.state is State.TRIAGE:
            item.state = State.OPEN
            intents.append(Intent(IntentKind.PROMOTED, item.group_id, item.tracking_id))

    if not item.is_terminal:
        intents += _sync_due_date(item, snap, cfg)

    new_state = next_state(item, counts, record.dynamic_status, snap.status_code)
    if new_state is not item.state:
        item.state = new_state
        item.resolved_at = now if item.is_terminal else None
        intents.append(Intent(STATE_CHANGE_INTENTS[new_state], item.group_id, item.tracking_id))
    elif not item.is_terminal and counts.pending != item.counts.pending:
        intents.append(
            Intent(
                IntentKind.PROGRESS,
                item.group_id,
                item.tracking_id,
                {"old_pending": item.counts.pending, "new_pending": counts.pending, "total": counts.total},
            )
        )
    item.counts = counts

    if needs_mismatch_flag(item, counts, now):
        item.verification_mismatch = True
        intents.append(Intent(IntentKind.MISMATCH, item.group_id, item.tracking_id, {"pending": counts.pending}))

    if item != original:
        repo.save_tracking(item)
        if item.state is not original.state:
            repo.append_audit(
                item.tracking_id,
                AuditEntry(now, "system", "STATE_CHANGE", original.state.value, item.state.value),
            )
    return intents


def _sync_due_date(item: TrackingItem, snap: EventSnapshot, cfg: Config) -> list[Intent]:
    due, source = compute_due(snap, to_local_date(item.created_at, cfg.timezone), cfg)
    aws_due = due if source is DueDateSource.AWS else None

    if item.due_date_source is DueDateSource.MANUAL:
        if aws_due is None or aws_due == item.aws_due_date:
            return []
        old, item.aws_due_date = item.aws_due_date, aws_due
        return [_due_intent(item, old, aws_due, DueDateSource.AWS, kept_manual=True)]

    if due == item.due_date and source is item.due_date_source:
        return []
    old = item.due_date
    item.due_date, item.due_date_source, item.aws_due_date = due, source, aws_due
    return [] if due == old else [_due_intent(item, old, due, source, kept_manual=False)]


def _due_intent(item, old, new, source, kept_manual) -> Intent:
    return Intent(
        IntentKind.DUE_CHANGED,
        item.group_id,
        item.tracking_id,
        {
            "old": old.isoformat() if old else None,
            "new": new.isoformat(),
            "source": source.value,
            "kept_manual": kept_manual,
        },
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_observe.py -v`
Expected: 19 passed

- [ ] **Step 6: Commit**

```bash
git add src/aha/domain/repository.py src/aha/domain/observe.py tests/fakes.py tests/test_observe.py
git commit -m "feat: add observe() core with grouping, due date sync and state transitions"
```

---

### Task 6: 수동 액션 (`apply_action`, `apply_group_action`)

**Files:**
- Create: `src/aha/domain/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `Repository`, `TrackingItem`, `Intent`, `ConcurrentModification`
- Produces: `InvalidAction(Exception)`, `ActionKind` (`ASSIGN`, `SET_DUE`, `RESOLVE_MANUAL`, `NOT_APPLICABLE`, `TRIAGE_REQUIRED`), `apply_action(repo, tracking_id, kind, actor, now, *, assignee=None, due=None, reason=None) -> TrackingItem`, `apply_group_action(repo, tracking_ids, kind, actor, now, *, channel, assignee=None, due=None, reason=None) -> list[Intent]` — 성공한 항목들을 묶어 `Intent(ACTION, group_id, None, {"action","actor","accounts","assignee","due","reason","channel"})` 1건(또는 빈 리스트) 반환. 단일 대상이면 `InvalidAction`을 그대로 전파, 다수 대상이면 종결 항목 등은 건너뜀.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_actions.py`:
```python
from datetime import date

import pytest

from aha.domain.actions import ActionKind, InvalidAction, apply_group_action
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Entity, IntentKind, State, Tier, tracking_id_of
from aha.domain.observe import observe
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import InMemoryRepository

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
OTHER_TID = tracking_id_of(GID, OTHER_ACCOUNT)


def _repo(**overrides):
    repo = InMemoryRepository()
    observe(make_snapshot(**overrides), repo, CFG, NOW)
    return repo


def _act(repo, kind, tids=(TID,), **kw):
    return apply_group_action(repo, list(tids), kind, "U1", NOW, channel="C1", **kw)


def test_assign_returns_single_action_intent():
    repo = _repo()
    intents = _act(repo, ActionKind.ASSIGN, assignee="U2")
    assert intents[0].kind is IntentKind.ACTION
    assert intents[0].tracking_id is None
    assert intents[0].data == {
        "action": "ASSIGN",
        "actor": "U1",
        "accounts": [ACCOUNT],
        "assignee": "U2",
        "due": None,
        "reason": None,
        "channel": "C1",
    }
    assert repo.get_tracking(TID).assignee == "U2"
    assert repo.list_audit(TID)[-1].action == "ASSIGN"


def test_set_due_marks_manual_and_resets_reminders():
    repo = _repo()
    item = repo.get_tracking(TID)
    item.reminders_sent = {"D-30"}
    repo.save_tracking(item)
    _act(repo, ActionKind.SET_DUE, due=date(2026, 12, 31), reason="벤더 일정")
    item = repo.get_tracking(TID)
    assert (item.due_date, item.due_date_source, item.reminders_sent) == (date(2026, 12, 31), DueDateSource.MANUAL, set())


def test_resolve_manual():
    repo = _repo()
    _act(repo, ActionKind.RESOLVE_MANUAL)
    item = repo.get_tracking(TID)
    assert (item.state, item.resolved_at, item.manual_resolved_at) == (State.RESOLVED_MANUAL, NOW, NOW)


def test_not_applicable_requires_reason():
    repo = _repo()
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.NOT_APPLICABLE, reason="  ")
    _act(repo, ActionKind.NOT_APPLICABLE, reason="해당 DB는 이미 마이그레이션 완료")
    assert repo.get_tracking(TID).state is State.NOT_APPLICABLE


def test_triage_required_promotes_triage_item():
    repo = _repo(actionability="ACTION_MAY_BE_REQUIRED")
    _act(repo, ActionKind.TRIAGE_REQUIRED)
    item = repo.get_tracking(TID)
    assert (item.state, item.tier) == (State.OPEN, Tier.REQUIRED)


def test_single_target_errors_propagate():
    repo = _repo()
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.TRIAGE_REQUIRED)
    _act(repo, ActionKind.RESOLVE_MANUAL)
    with pytest.raises(InvalidAction):
        _act(repo, ActionKind.RESOLVE_MANUAL)


def test_group_action_skips_terminal_items():
    repo = _repo()
    observe(make_snapshot(account_id=OTHER_ACCOUNT, entities=(Entity("db-9", "RESOLVED"),)), repo, CFG, NOW)
    intents = _act(repo, ActionKind.ASSIGN, tids=(TID, OTHER_TID), assignee="U2")
    assert intents[0].data["accounts"] == [ACCOUNT]
    assert repo.get_tracking(OTHER_TID).assignee is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_actions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.domain.actions'`

- [ ] **Step 3: 구현**

`src/aha/domain/actions.py`:
```python
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from aha.domain.models import AuditEntry, DueDateSource, Intent, IntentKind, State, Tier, TrackingItem
from aha.domain.repository import ConcurrentModification, Repository

MAX_ATTEMPTS = 3


class InvalidAction(Exception):
    """사용자 액션을 적용할 수 없음 (메시지는 Slack에 그대로 노출된다)."""


class ActionKind(StrEnum):
    ASSIGN = "ASSIGN"
    SET_DUE = "SET_DUE"
    RESOLVE_MANUAL = "RESOLVE_MANUAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TRIAGE_REQUIRED = "TRIAGE_REQUIRED"


def _validate_args(kind: ActionKind, assignee: str | None, due: date | None, reason: str | None) -> None:
    if kind is ActionKind.ASSIGN and not assignee:
        raise InvalidAction("담당자를 선택하세요")
    if kind is ActionKind.SET_DUE and due is None:
        raise InvalidAction("Due Date를 입력하세요")
    if kind is ActionKind.NOT_APPLICABLE and (not reason or not reason.strip()):
        raise InvalidAction("사유를 입력하세요")


def apply_action(
    repo: Repository,
    tracking_id: str,
    kind: ActionKind,
    actor: str,
    now: datetime,
    *,
    assignee: str | None = None,
    due: date | None = None,
    reason: str | None = None,
) -> TrackingItem:
    _validate_args(kind, assignee, due, reason)
    item = repo.get_tracking(tracking_id)
    if item is None:
        raise InvalidAction(f"추적 항목을 찾을 수 없습니다: {tracking_id}")
    if item.is_terminal:
        raise InvalidAction("이미 종결된 항목입니다")
    before = item.state

    if kind is ActionKind.ASSIGN:
        item.assignee = assignee
    elif kind is ActionKind.SET_DUE:
        item.due_date, item.due_date_source = due, DueDateSource.MANUAL
        item.reminders_sent = set()
    elif kind is ActionKind.RESOLVE_MANUAL:
        item.state = State.RESOLVED_MANUAL
        item.resolved_at = item.manual_resolved_at = now
    elif kind is ActionKind.NOT_APPLICABLE:
        item.state, item.resolved_at = State.NOT_APPLICABLE, now
    elif kind is ActionKind.TRIAGE_REQUIRED:
        if item.state is not State.TRIAGE:
            raise InvalidAction("검토 대기 상태가 아닙니다")
        item.state, item.tier = State.OPEN, Tier.REQUIRED

    repo.save_tracking(item)
    detail = reason or assignee or (due.isoformat() if due else None)
    repo.append_audit(tracking_id, AuditEntry(now, actor, kind.value, before.value, item.state.value, detail))
    return item


def apply_group_action(
    repo: Repository,
    tracking_ids: list[str],
    kind: ActionKind,
    actor: str,
    now: datetime,
    *,
    channel: str,
    assignee: str | None = None,
    due: date | None = None,
    reason: str | None = None,
) -> list[Intent]:
    _validate_args(kind, assignee, due, reason)
    applied: list[TrackingItem] = []
    for tracking_id in tracking_ids:
        try:
            applied.append(_apply_with_retry(repo, tracking_id, kind, actor, now, assignee, due, reason))
        except InvalidAction:
            if len(tracking_ids) == 1:
                raise
    if not applied:
        return []
    return [
        Intent(
            IntentKind.ACTION,
            applied[0].group_id,
            None,
            {
                "action": kind.value,
                "actor": actor,
                "accounts": [i.account_id for i in applied],
                "assignee": assignee,
                "due": due.isoformat() if due else None,
                "reason": reason,
                "channel": channel,
            },
        )
    ]


def _apply_with_retry(repo, tracking_id, kind, actor, now, assignee, due, reason) -> TrackingItem:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return apply_action(repo, tracking_id, kind, actor, now, assignee=assignee, due=due, reason=reason)
        except ConcurrentModification:
            if attempt == MAX_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_actions.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/domain/actions.py tests/test_actions.py
git commit -m "feat: add manual Slack actions with audit trail"
```

---

### Task 7: DynamoDB Repository (계약 테스트 공유)

**Files:**
- Create: `src/aha/adapters/repo_dynamodb.py`
- Create: `tests/dynamo_util.py`
- Test: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: `Repository` 계약(Task 5), 모델(Task 1)
- Produces: `DynamoRepository(table)` — boto3 `Table` 리소스를 받는다. 키 레이아웃은 spec 7.2와 동일. GSI 이름 `gsi-state` (`gsi1pk`, `gsi1sk`).
- Produces (tests): `tests.dynamo_util.create_state_table(resource, name="aha-test-state")`

- [ ] **Step 1: 실패하는 계약 테스트 작성 (Fake와 Dynamo 모두에 대해 실행)**

`tests/dynamo_util.py`:
```python
def create_state_table(resource, name="aha-test-state"):
    return resource.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "gsi1pk", "gsi1sk")],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi-state",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )
```

`tests/test_repository_contract.py`:
```python
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from aha.adapters.repo_dynamodb import DynamoRepository
from aha.domain.models import (
    AuditEntry,
    DueDateSource,
    Entity,
    EntityCounts,
    EventGroup,
    EventRecord,
    Notice,
    State,
    Tier,
)
from aha.domain.repository import ConcurrentModification
from tests.dynamo_util import create_state_table
from tests.factories import ACCOUNT, ARN, NOW, OTHER_ACCOUNT, make_item
from tests.fakes import InMemoryRepository


@pytest.fixture(params=["memory", "dynamo"])
def repo(request):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    with mock_aws():
        table = create_state_table(boto3.resource("dynamodb", region_name="ap-northeast-2"))
        yield DynamoRepository(table)


def _record(**overrides):
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
        personas=("OPERATIONS", "SECURITY"),
        description="desc",
        description_hash="h",
        dynamic_status=True,
    )
    base.update(overrides)
    return EventRecord(**base)


def test_event_roundtrip(repo):
    repo.put_event(_record())
    assert repo.get_event(ARN) == _record()
    assert repo.get_event("missing") is None


def test_group_put_merges_arns(repo):
    repo.put_group(EventGroup("g1", "key", "arn-1", {"arn-1"}))
    repo.put_group(EventGroup("g1", "key", "arn-2", {"arn-2"}))
    group = repo.get_group("g1")
    assert (group.primary_arn, group.event_arns) == ("arn-1", {"arn-1", "arn-2"})


def test_create_tracking_is_conditional(repo):
    item = make_item()
    assert repo.create_tracking(item) is True
    assert item.version == 1
    assert repo.create_tracking(make_item()) is False


def test_full_tracking_roundtrip(repo):
    item = make_item(
        tier=Tier.REVIEW,
        state=State.TRIAGE,
        due_date_source=DueDateSource.MANUAL,
        event_arns={"arn-1", "arn-2"},
        aws_due_date=date(2026, 12, 1),
        assignee="U2",
        reminders_sent={"D-30"},
        manual_completion_required=True,
        verification_mismatch=True,
        counts=EntityCounts(1, 2, 3),
        resolved_at=NOW,
        manual_resolved_at=NOW,
    )
    repo.create_tracking(item)
    assert repo.get_tracking(item.tracking_id) == item


def test_save_tracking_detects_conflict(repo):
    repo.create_tracking(make_item())
    first, second = repo.get_tracking(f"g1#{ACCOUNT}"), repo.get_tracking(f"g1#{ACCOUNT}")
    first.assignee = "U1"
    repo.save_tracking(first)
    assert first.version == 2
    second.assignee = "U2"
    with pytest.raises(ConcurrentModification):
        repo.save_tracking(second)


def test_list_group_and_state(repo):
    repo.create_tracking(make_item(account_id=OTHER_ACCOUNT, due_date=date(2026, 11, 1)))
    repo.create_tracking(make_item(account_id=ACCOUNT, due_date=date(2026, 12, 1)))
    repo.create_tracking(make_item(group_id="g2", state=State.TRIAGE))
    assert [i.account_id for i in repo.list_group_tracking("g1")] == [ACCOUNT, OTHER_ACCOUNT]
    assert [i.account_id for i in repo.list_by_state(State.OPEN)] == [OTHER_ACCOUNT, ACCOUNT]
    assert [i.group_id for i in repo.list_by_state(State.TRIAGE)] == ["g2"]


def test_entities_upsert_overwrites(repo):
    tid = f"g1#{ACCOUNT}"
    repo.upsert_entities(tid, [Entity("db-1", "PENDING"), Entity("db-2", "PENDING")], NOW)
    repo.upsert_entities(tid, [Entity("db-1", "RESOLVED")], NOW)
    assert repo.list_entities(tid) == [Entity("db-1", "RESOLVED"), Entity("db-2", "PENDING")]


def test_notices_cursor_and_audit(repo):
    repo.put_notice(Notice("g1", "C1", "1.0", "C1"))
    repo.put_notice(Notice("g1", "C2", "2.0", "C0DEFAULT"))
    assert repo.get_notices("g1") == [Notice("g1", "C1", "1.0", "C1"), Notice("g1", "C2", "2.0", "C0DEFAULT")]
    assert repo.get_cursor("event") is None
    repo.set_cursor("event", NOW)
    assert repo.get_cursor("event") == NOW
    entry = AuditEntry(NOW, "U1", "ASSIGN", "OPEN", "OPEN", "U2")
    repo.append_audit(f"g1#{ACCOUNT}", entry)
    assert repo.list_audit(f"g1#{ACCOUNT}") == [entry]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_repository_contract.py -v`
Expected: memory 파라미터는 PASS, 수집 단계에서 `ModuleNotFoundError: No module named 'aha.adapters.repo_dynamodb'` 로 전체 FAIL

- [ ] **Step 3: 구현**

`src/aha/adapters/repo_dynamodb.py`:
```python
from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from datetime import date, datetime

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from aha.domain.models import (
    AuditEntry,
    DueDateSource,
    Entity,
    EntityCounts,
    EventGroup,
    EventRecord,
    Notice,
    State,
    Tier,
    TrackingItem,
    split_tracking_id,
)
from aha.domain.repository import ConcurrentModification

STATE_INDEX = "gsi-state"


class DynamoRepository:
    def __init__(self, table) -> None:
        self._t = table

    # --- Event ---
    def get_event(self, event_arn: str) -> EventRecord | None:
        item = self._get(f"EVT#{event_arn}", "META")
        return _event_from(item) if item else None

    def put_event(self, record: EventRecord) -> None:
        self._t.put_item(Item={"pk": f"EVT#{record.event_arn}", "sk": "META", **_event_to(record)})

    # --- Group ---
    def get_group(self, group_id: str) -> EventGroup | None:
        item = self._get(f"GRP#{group_id}", "META")
        if not item:
            return None
        return EventGroup(group_id, item["correlation_key"], item["primary_arn"], set(item["event_arns"]))

    def put_group(self, group: EventGroup) -> None:
        self._t.update_item(
            Key={"pk": f"GRP#{group.group_id}", "sk": "META"},
            UpdateExpression=(
                "SET correlation_key = if_not_exists(correlation_key, :k), "
                "primary_arn = if_not_exists(primary_arn, :p) ADD event_arns :a"
            ),
            ExpressionAttributeValues={":k": group.correlation_key, ":p": group.primary_arn, ":a": set(group.event_arns)},
        )

    # --- Tracking ---
    def get_tracking(self, tracking_id: str) -> TrackingItem | None:
        group_id, account_id = split_tracking_id(tracking_id)
        item = self._get(f"GRP#{group_id}", f"TRK#{account_id}")
        return _tracking_from(item) if item else None

    def create_tracking(self, item: TrackingItem) -> bool:
        try:
            self._t.put_item(Item=_tracking_to(item, version=1), ConditionExpression="attribute_not_exists(pk)")
        except ClientError as e:
            if _conditional_failed(e):
                return False
            raise
        item.version = 1
        return True

    def save_tracking(self, item: TrackingItem) -> None:
        try:
            self._t.put_item(
                Item=_tracking_to(item, version=item.version + 1),
                ConditionExpression="version = :v",
                ExpressionAttributeValues={":v": item.version},
            )
        except ClientError as e:
            if _conditional_failed(e):
                raise ConcurrentModification(item.tracking_id) from e
            raise
        item.version += 1

    def list_group_tracking(self, group_id: str) -> list[TrackingItem]:
        cond = Key("pk").eq(f"GRP#{group_id}") & Key("sk").begins_with("TRK#")
        return [_tracking_from(i) for i in self._query(cond)]

    def list_by_state(self, state: State) -> list[TrackingItem]:
        return [_tracking_from(i) for i in self._query(Key("gsi1pk").eq(f"STATE#{state.value}"), index=STATE_INDEX)]

    # --- Entity ---
    def upsert_entities(self, tracking_id: str, entities: Iterable[Entity], now: datetime) -> None:
        with self._t.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for e in entities:
                batch.put_item(
                    Item={"pk": f"TRK#{tracking_id}", "sk": f"ENT#{e.value}", "status": e.status, "last_seen_at": now.isoformat()}
                )

    def list_entities(self, tracking_id: str) -> list[Entity]:
        cond = Key("pk").eq(f"TRK#{tracking_id}") & Key("sk").begins_with("ENT#")
        return [Entity(i["sk"].removeprefix("ENT#"), i["status"]) for i in self._query(cond)]

    # --- Audit ---
    def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        self._t.put_item(
            Item={
                "pk": f"TRK#{tracking_id}",
                "sk": f"LOG#{entry.at.isoformat()}#{uuid.uuid4().hex[:8]}",
                "actor": entry.actor,
                "action": entry.action,
                "before": entry.before,
                "after": entry.after,
                "detail": entry.detail,
            }
        )

    def list_audit(self, tracking_id: str) -> list[AuditEntry]:
        cond = Key("pk").eq(f"TRK#{tracking_id}") & Key("sk").begins_with("LOG#")
        return [
            AuditEntry(datetime.fromisoformat(i["sk"].split("#")[1]), i["actor"], i["action"], i.get("before"), i.get("after"), i.get("detail"))
            for i in self._query(cond)
        ]

    # --- Notice ---
    def get_notices(self, group_id: str) -> list[Notice]:
        cond = Key("pk").eq(f"GRP#{group_id}") & Key("sk").begins_with("NTC#")
        return [Notice(group_id, i["sk"].removeprefix("NTC#"), i["ts"], i["posted_channel_id"]) for i in self._query(cond)]

    def put_notice(self, notice: Notice) -> None:
        self._t.put_item(
            Item={
                "pk": f"GRP#{notice.group_id}",
                "sk": f"NTC#{notice.channel_id}",
                "ts": notice.ts,
                "posted_channel_id": notice.posted_channel_id,
            }
        )

    # --- Cursor ---
    def get_cursor(self, name: str) -> datetime | None:
        item = self._get("CURSOR", name)
        return datetime.fromisoformat(item["value"]) if item else None

    def set_cursor(self, name: str, value: datetime) -> None:
        self._t.put_item(Item={"pk": "CURSOR", "sk": name, "value": value.isoformat()})

    # --- helpers ---
    def _get(self, pk: str, sk: str) -> dict | None:
        return self._t.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")

    def _query(self, condition, index: str | None = None) -> Iterator[dict]:
        kwargs: dict = {"KeyConditionExpression": condition}
        if index:
            kwargs["IndexName"] = index
        else:
            kwargs["ConsistentRead"] = True
        while True:
            resp = self._t.query(**kwargs)
            yield from resp.get("Items", [])
            if "LastEvaluatedKey" not in resp:
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _conditional_failed(e: ClientError) -> bool:
    return e.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _event_to(r: EventRecord) -> dict:
    return {
        "group_id": r.group_id,
        "service": r.service,
        "event_type_code": r.event_type_code,
        "category": r.category,
        "event_region": r.event_region,
        "status_code": r.status_code,
        "start_time": _iso(r.start_time),
        "end_time": _iso(r.end_time),
        "last_updated_time": r.last_updated_time.isoformat(),
        "actionability": r.actionability,
        "personas": list(r.personas),
        "description": r.description,
        "description_hash": r.description_hash,
        "dynamic_status": r.dynamic_status,
    }


def _event_from(i: dict) -> EventRecord:
    return EventRecord(
        event_arn=i["pk"].removeprefix("EVT#"),
        group_id=i["group_id"],
        service=i["service"],
        event_type_code=i["event_type_code"],
        category=i["category"],
        event_region=i["event_region"],
        status_code=i["status_code"],
        start_time=_dt(i.get("start_time")),
        end_time=_dt(i.get("end_time")),
        last_updated_time=datetime.fromisoformat(i["last_updated_time"]),
        actionability=i.get("actionability"),
        personas=tuple(i.get("personas") or ()),
        description=i.get("description"),
        description_hash=i.get("description_hash"),
        dynamic_status=bool(i["dynamic_status"]),
    )


def _tracking_to(t: TrackingItem, version: int) -> dict:
    return {
        "pk": f"GRP#{t.group_id}",
        "sk": f"TRK#{t.account_id}",
        "gsi1pk": f"STATE#{t.state.value}",
        "gsi1sk": f"{t.due_date.isoformat()}#{t.tracking_id}",
        "group_id": t.group_id,
        "account_id": t.account_id,
        "tier": t.tier.value,
        "state": t.state.value,
        "due_date": t.due_date.isoformat(),
        "due_date_source": t.due_date_source.value,
        "created_at": t.created_at.isoformat(),
        "event_arns": sorted(t.event_arns),
        "aws_due_date": _iso(t.aws_due_date),
        "assignee": t.assignee,
        "reminders_sent": sorted(t.reminders_sent),
        "manual_completion_required": t.manual_completion_required,
        "verification_mismatch": t.verification_mismatch,
        "counts": {"pending": t.counts.pending, "resolved": t.counts.resolved, "total": t.counts.total},
        "resolved_at": _iso(t.resolved_at),
        "manual_resolved_at": _iso(t.manual_resolved_at),
        "version": version,
    }


def _tracking_from(i: dict) -> TrackingItem:
    c = i["counts"]
    return TrackingItem(
        group_id=i["group_id"],
        account_id=i["account_id"],
        tier=Tier(i["tier"]),
        state=State(i["state"]),
        due_date=date.fromisoformat(i["due_date"]),
        due_date_source=DueDateSource(i["due_date_source"]),
        created_at=datetime.fromisoformat(i["created_at"]),
        event_arns=set(i.get("event_arns") or ()),
        aws_due_date=_d(i.get("aws_due_date")),
        assignee=i.get("assignee"),
        reminders_sent=set(i.get("reminders_sent") or ()),
        manual_completion_required=bool(i["manual_completion_required"]),
        verification_mismatch=bool(i["verification_mismatch"]),
        counts=EntityCounts(int(c["pending"]), int(c["resolved"]), int(c["total"])),
        resolved_at=_dt(i.get("resolved_at")),
        manual_resolved_at=_dt(i.get("manual_resolved_at")),
        version=int(i["version"]),
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_repository_contract.py -v`
Expected: 16 passed (8 tests × memory/dynamo)

- [ ] **Step 5: Commit**

```bash
git add src/aha/adapters/repo_dynamodb.py tests/dynamo_util.py tests/test_repository_contract.py
git commit -m "feat: add DynamoDB repository with shared contract tests"
```

---

### Task 8: Health Organizational API 클라이언트

**Files:**
- Create: `src/aha/adapters/health_client.py`
- Test: `tests/test_health_client.py`

**Interfaces:**
- Consumes: `Entity`, `EventSnapshot`
- Produces: `HealthClient(client=None)` with `iter_events(*, updated_since: datetime | None, status_codes: list[str]) -> Iterator[dict]`, `affected_accounts(event_arn) -> list[str]`, `event_description(event_arn, account_id) -> str | None`, `affected_entities(event_arn, account_id) -> list[Entity]`
- Produces: `to_snapshot(event: dict, account_id: str, description: str | None, entities: list[Entity], source: str = "RECONCILE") -> EventSnapshot`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_health_client.py`:
```python
from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber

from aha.adapters.health_client import HealthClient, to_snapshot
from aha.domain.models import Entity

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def _stubbed():
    client = boto3.client("health", region_name="us-east-1")
    return client, Stubber(client)


def test_iter_events_paginates_with_filter():
    client, stub = _stubbed()
    flt = {"eventStatusCodes": ["open", "upcoming", "closed"], "lastUpdatedTime": {"from": SINCE}}
    stub.add_response("describe_events_for_organization", {"events": [{"arn": "a1"}], "nextToken": "t"}, {"filter": flt})
    stub.add_response("describe_events_for_organization", {"events": [{"arn": "a2"}]}, {"filter": flt, "nextToken": "t"})
    with stub:
        events = list(HealthClient(client).iter_events(updated_since=SINCE, status_codes=["open", "upcoming", "closed"]))
    assert [e["arn"] for e in events] == ["a1", "a2"]


def test_iter_events_without_since():
    client, stub = _stubbed()
    stub.add_response("describe_events_for_organization", {"events": []}, {"filter": {"eventStatusCodes": ["open"]}})
    with stub:
        assert list(HealthClient(client).iter_events(updated_since=None, status_codes=["open"])) == []


def test_affected_accounts():
    client, stub = _stubbed()
    stub.add_response(
        "describe_affected_accounts_for_organization",
        {"affectedAccounts": ["111122223333", "444455556666"]},
        {"eventArn": "a1"},
    )
    with stub:
        assert HealthClient(client).affected_accounts("a1") == ["111122223333", "444455556666"]


def test_event_description():
    client, stub = _stubbed()
    stub.add_response(
        "describe_event_details_for_organization",
        {"successfulSet": [{"awsAccountId": "111122223333", "eventDescription": {"latestDescription": "hello"}}], "failedSet": []},
        {"organizationEventDetailFilters": [{"eventArn": "a1", "awsAccountId": "111122223333"}]},
    )
    with stub:
        assert HealthClient(client).event_description("a1", "111122223333") == "hello"


def test_affected_entities():
    client, stub = _stubbed()
    stub.add_response(
        "describe_affected_entities_for_organization",
        {"entities": [{"entityValue": "db-1", "statusCode": "PENDING"}, {"entityValue": "db-2"}]},
        {"organizationEntityAccountFilters": [{"eventArn": "a1", "awsAccountId": "111122223333"}]},
    )
    with stub:
        assert HealthClient(client).affected_entities("a1", "111122223333") == [
            Entity("db-1", "PENDING"),
            Entity("db-2", "UNKNOWN"),
        ]


def test_to_snapshot_maps_fields():
    event = {
        "arn": "a1",
        "service": "RDS",
        "eventTypeCode": "AWS_RDS_PLANNED_LIFECYCLE_EVENT",
        "eventTypeCategory": "scheduledChange",
        "region": "ap-northeast-2",
        "statusCode": "upcoming",
        "startTime": datetime(2026, 11, 30, tzinfo=UTC),
        "lastUpdatedTime": SINCE,
        "actionability": "ACTION_REQUIRED",
        "personas": ["OPERATIONS"],
    }
    snap = to_snapshot(event, "111122223333", "desc", [Entity("db-1", "PENDING")])
    assert (snap.event_arn, snap.account_id, snap.category, snap.actionability) == (
        "a1", "111122223333", "scheduledChange", "ACTION_REQUIRED"
    )
    assert snap.personas == ("OPERATIONS",)
    assert snap.entities == (Entity("db-1", "PENDING"),)
    assert snap.end_time is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_health_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.adapters.health_client'`

- [ ] **Step 3: 구현**

`src/aha/adapters/health_client.py`:
```python
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
from botocore.config import Config as BotoConfig

from aha.domain.models import Entity, EventSnapshot

HEALTH_REGION = "us-east-1"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class HealthClient:
    """AWS Health Organizational view 읽기 전용 래퍼 (위임 관리자 계정에서 호출)."""

    def __init__(self, client=None) -> None:
        self._client = client or boto3.client(
            "health",
            region_name=HEALTH_REGION,
            config=BotoConfig(retries={"mode": "adaptive", "max_attempts": 10}),
        )

    def iter_events(self, *, updated_since: datetime | None, status_codes: list[str]) -> Iterator[dict]:
        flt: dict = {"eventStatusCodes": list(status_codes)}
        if updated_since is not None:
            flt["lastUpdatedTime"] = {"from": updated_since}
        for page in self._client.get_paginator("describe_events_for_organization").paginate(filter=flt):
            yield from page.get("events", [])

    def affected_accounts(self, event_arn: str) -> list[str]:
        accounts: list[str] = []
        for page in self._client.get_paginator("describe_affected_accounts_for_organization").paginate(eventArn=event_arn):
            accounts += page.get("affectedAccounts", [])
        return accounts

    def event_description(self, event_arn: str, account_id: str) -> str | None:
        resp = self._client.describe_event_details_for_organization(
            organizationEventDetailFilters=[{"eventArn": event_arn, "awsAccountId": account_id}]
        )
        for detail in resp.get("successfulSet", []):
            return detail.get("eventDescription", {}).get("latestDescription")
        return None

    def affected_entities(self, event_arn: str, account_id: str) -> list[Entity]:
        entities: list[Entity] = []
        paginator = self._client.get_paginator("describe_affected_entities_for_organization")
        for page in paginator.paginate(organizationEntityAccountFilters=[{"eventArn": event_arn, "awsAccountId": account_id}]):
            entities += [
                Entity(e["entityValue"], e.get("statusCode", "UNKNOWN")) for e in page.get("entities", []) if "entityValue" in e
            ]
        return entities


def to_snapshot(
    event: dict, account_id: str, description: str | None, entities: list[Entity], source: str = "RECONCILE"
) -> EventSnapshot:
    return EventSnapshot(
        event_arn=event["arn"],
        account_id=account_id,
        service=event.get("service", ""),
        event_type_code=event["eventTypeCode"],
        category=event.get("eventTypeCategory", ""),
        event_region=event.get("region", "global"),
        status_code=event.get("statusCode", "open"),
        start_time=event.get("startTime"),
        end_time=event.get("endTime"),
        last_updated_time=event.get("lastUpdatedTime") or event.get("startTime") or EPOCH,
        actionability=event.get("actionability"),
        personas=tuple(event.get("personas") or ()),
        description=description,
        entities=tuple(entities),
        source=source,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_health_client.py -v`
Expected: 6 passed

> 만약 `ParamValidationError: Unknown parameter ... organizationEntityAccountFilters`가 나오면 `pip install -U boto3`로 botocore를 최신화한다. 해당 파라미터는 엔티티 상태 필터를 지원하는 현재 API 형식이다.

- [ ] **Step 5: Commit**

```bash
git add src/aha/adapters/health_client.py tests/test_health_client.py
git commit -m "feat: add Health organizational view client"
```

---

### Task 9: Router, Intent Publisher, Reconciler

**Files:**
- Create: `src/aha/routing.py`, `src/aha/adapters/intent_queue.py`, `src/aha/reconcile.py`
- Modify: `tests/fakes.py` (RecordingPublisher, FakeHealth, MapRouter 추가)
- Test: `tests/test_intent_queue.py`, `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `HealthClient` 메서드 시그니처(Task 8), `to_snapshot`, `observe_with_retry`, `Repository`
- Produces: `Router` Protocol (`channels_for(account_id, personas) -> list[str]`, `account_name(account_id) -> str | None`), `StaticRouter(channel)`, `items_for_channel(items, channel, router, personas) -> list[TrackingItem]`
- Produces: `SqsIntentPublisher(client, queue_url).publish(intents: Iterable[Intent]) -> None` (MessageGroupId=group_id, 10건 배치)
- Produces: `ReconcileResult(events=0, observations=0, created=0)`, `Reconciler(health, repo, publisher, router, cfg).run(mode: "event"|"entity"|"full", now) -> ReconcileResult`, `CURSOR_OVERLAP = timedelta(hours=2)`

- [ ] **Step 1: Fake 추가**

`tests/fakes.py` 하단에 추가:
```python
class RecordingPublisher:
    def __init__(self):
        self.intents = []

    def publish(self, intents):
        self.intents.extend(intents)


class FakeHealth:
    def __init__(self, events, accounts, entities, descriptions=None):
        self.events = events
        self.accounts = accounts
        self.entities = entities
        self.descriptions = descriptions or {}
        self.calls = []

    def iter_events(self, *, updated_since, status_codes):
        self.calls.append((updated_since, tuple(status_codes)))
        return iter(self.events)

    def affected_accounts(self, event_arn):
        return self.accounts.get(event_arn, [])

    def event_description(self, event_arn, account_id):
        return self.descriptions.get(event_arn)

    def affected_entities(self, event_arn, account_id):
        return list(self.entities.get((event_arn, account_id), []))


class MapRouter:
    def __init__(self, mapping, names=None):
        self.mapping = mapping
        self.names = names or {}

    def channels_for(self, account_id, personas):
        return [self.mapping[account_id]]

    def account_name(self, account_id):
        return self.names.get(account_id)
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_intent_queue.py`:
```python
import boto3
from moto import mock_aws

from aha.adapters.intent_queue import SqsIntentPublisher
from aha.domain.models import Intent, IntentKind


@mock_aws
def test_publish_batches_with_group_ids():
    sqs = boto3.client("sqs", region_name="ap-northeast-2")
    url = sqs.create_queue(QueueName="t.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    intents = [Intent(IntentKind.REFRESH, f"g{n}", data={"n": n}) for n in range(12)]
    SqsIntentPublisher(sqs, url).publish(intents)

    received = []
    while True:
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, AttributeNames=["MessageGroupId"]).get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            received.append(m)
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
    assert sorted(Intent.from_json(m["Body"]).data["n"] for m in received) == list(range(12))
    assert {m["Attributes"]["MessageGroupId"] for m in received} == {f"g{n}" for n in range(12)}


def test_publish_nothing_makes_no_call():
    class Boom:
        def send_message_batch(self, **kwargs):
            raise AssertionError("should not be called")

    SqsIntentPublisher(Boom(), "url").publish([])
```

`tests/test_reconcile.py`:
```python
from datetime import UTC, datetime, timedelta

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import Entity, IntentKind, Notice
from aha.reconcile import CURSOR_OVERLAP, ReconcileResult, Reconciler
from aha.routing import StaticRouter
from tests.factories import ACCOUNT, ARN, NOW, make_config, make_snapshot
from tests.fakes import FakeHealth, InMemoryRepository, RecordingPublisher

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
EVENT = {
    "arn": ARN,
    "service": "RDS",
    "eventTypeCode": "AWS_RDS_PLANNED_LIFECYCLE_EVENT",
    "eventTypeCategory": "scheduledChange",
    "region": "ap-northeast-2",
    "eventScopeCode": "ACCOUNT_SPECIFIC",
    "statusCode": "upcoming",
    "startTime": datetime(2026, 11, 30, tzinfo=UTC),
    "lastUpdatedTime": datetime(2026, 10, 1, tzinfo=UTC),
    "actionability": "ACTION_REQUIRED",
    "personas": ["OPERATIONS"],
}


def _health(events=(EVENT,)):
    return FakeHealth(list(events), {ARN: [ACCOUNT]}, {(ARN, ACCOUNT): [Entity("db-1", "PENDING")]}, {ARN: "desc"})


def _reconciler(health):
    repo, pub = InMemoryRepository(), RecordingPublisher()
    return Reconciler(health, repo, pub, StaticRouter("C0SHADOW"), CFG), repo, pub


def test_first_event_sweep_uses_open_upcoming_and_sets_cursor():
    health = _health()
    rec, repo, pub = _reconciler(health)
    result = rec.run("event", NOW)
    assert health.calls == [(None, ("open", "upcoming"))]
    assert result == ReconcileResult(events=1, observations=1, created=1)
    assert [i.kind for i in pub.intents] == [IntentKind.NEW]
    assert repo.get_event(ARN).description == "desc"
    assert repo.get_cursor("event") == NOW


def test_next_event_sweep_overlaps_cursor_and_includes_closed():
    health = _health()
    rec, repo, _ = _reconciler(health)
    repo.set_cursor("event", NOW)
    rec.run("event", NOW + timedelta(hours=1))
    assert health.calls == [(NOW - CURSOR_OVERLAP, ("open", "upcoming", "closed"))]


def test_skips_informational_and_public_events():
    events = [{**EVENT, "arn": "a-info", "actionability": "INFORMATIONAL"}, {**EVENT, "arn": "a-pub", "eventScopeCode": "PUBLIC"}]
    rec, _, pub = _reconciler(_health(events))
    assert rec.run("event", NOW).events == 0
    assert pub.intents == []


def test_full_resync_counts_created_and_keeps_cursor():
    health = _health()
    rec, repo, _ = _reconciler(health)
    result = rec.run("full", NOW)
    assert health.calls == [(None, ("open", "upcoming"))]
    assert result.created == 1
    assert repo.get_cursor("event") is None


def test_entity_sweep_resolves_items():
    health = _health()
    rec, repo, pub = _reconciler(health)
    rec.run("event", NOW)
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    health.entities[(ARN, ACCOUNT)] = [Entity("db-1", "RESOLVED")]
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=6))
    assert [i.kind for i in pub.intents] == [IntentKind.RESOLVED]


def test_entity_sweep_republishes_missing_notice():
    rec, repo, pub = _reconciler(_health())
    rec.run("event", NOW)
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=6))
    assert [(i.kind, i.group_id) for i in pub.intents] == [(IntentKind.REFRESH, GID)]

    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0SHADOW"))
    pub.intents.clear()
    rec.run("entity", NOW + timedelta(hours=12))
    assert pub.intents == []
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_intent_queue.py tests/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.adapters.intent_queue'`

- [ ] **Step 4: 구현**

`src/aha/routing.py`:
```python
from __future__ import annotations

from typing import Protocol

from aha.domain.models import TrackingItem


class Router(Protocol):
    def channels_for(self, account_id: str, personas: tuple[str, ...]) -> list[str]: ...
    def account_name(self, account_id: str) -> str | None: ...


class StaticRouter:
    """P1 섀도 운영: 모든 계정을 단일 채널로 보낸다. P2에서 계정 태그 기반 Router로 교체한다."""

    def __init__(self, channel: str) -> None:
        self._channel = channel

    def channels_for(self, account_id: str, personas: tuple[str, ...]) -> list[str]:
        return [self._channel]

    def account_name(self, account_id: str) -> str | None:
        return None


def items_for_channel(
    items: list[TrackingItem], channel: str, router: Router, personas: tuple[str, ...]
) -> list[TrackingItem]:
    return [i for i in items if channel in router.channels_for(i.account_id, personas)]
```

`src/aha/adapters/intent_queue.py`:
```python
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from aha.domain.models import Intent

SQS_BATCH_LIMIT = 10


class SqsIntentPublisher:
    def __init__(self, client, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def publish(self, intents: Iterable[Intent]) -> None:
        pending = list(intents)
        for start in range(0, len(pending), SQS_BATCH_LIMIT):
            entries = []
            for n, intent in enumerate(pending[start : start + SQS_BATCH_LIMIT]):
                body = intent.to_json()
                entries.append(
                    {
                        "Id": str(n),
                        "MessageBody": body,
                        # 그룹 단위 직렬 처리로 Notice 중복 게시를 막는다.
                        "MessageGroupId": intent.group_id,
                        "MessageDeduplicationId": hashlib.sha256(body.encode()).hexdigest(),
                    }
                )
            resp = self._client.send_message_batch(QueueUrl=self._queue_url, Entries=entries)
            if resp.get("Failed"):
                raise RuntimeError(f"intent publish failed: {resp['Failed']}")
```

`src/aha/reconcile.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aha.adapters.health_client import to_snapshot
from aha.config import Config
from aha.domain.models import EventSnapshot, Intent, IntentKind, State
from aha.domain.observe import observe_with_retry
from aha.domain.repository import Repository
from aha.routing import Router

CURSOR_OVERLAP = timedelta(hours=2)
EVENT_CURSOR = "event"
# RESOLVED_AUTO는 재발 감지, RESOLVED_MANUAL은 불일치 검증을 위해 계속 조회한다.
ENTITY_SWEEP_STATES = (State.TRIAGE, State.OPEN, State.RESOLVED_AUTO, State.RESOLVED_MANUAL)


@dataclass
class ReconcileResult:
    events: int = 0
    observations: int = 0
    created: int = 0


class Reconciler:
    def __init__(self, health, repo: Repository, publisher, router: Router, cfg: Config) -> None:
        self._health = health
        self._repo = repo
        self._publisher = publisher
        self._router = router
        self._cfg = cfg

    def run(self, mode: str, now: datetime) -> ReconcileResult:
        if mode in ("event", "full"):
            return self._event_sweep(mode, now)
        if mode == "entity":
            return self._entity_sweep(now)
        raise ValueError(f"unknown reconcile mode: {mode}")

    def _event_sweep(self, mode: str, now: datetime) -> ReconcileResult:
        result = ReconcileResult()
        cursor = self._repo.get_cursor(EVENT_CURSOR) if mode == "event" else None
        since = cursor - CURSOR_OVERLAP if cursor else None
        statuses = ["open", "upcoming"] if since is None else ["open", "upcoming", "closed"]

        for event in self._health.iter_events(updated_since=since, status_codes=statuses):
            if self._skip(event):
                continue
            result.events += 1
            accounts = self._health.affected_accounts(event["arn"])
            description = self._health.event_description(event["arn"], accounts[0]) if accounts else None
            for account_id in accounts:
                entities = self._health.affected_entities(event["arn"], account_id)
                self._observe(to_snapshot(event, account_id, description, entities), now, result)

        if mode == "event":
            # 실패 시 이 줄에 도달하지 않으므로 다음 실행이 같은 구간을 다시 본다.
            self._repo.set_cursor(EVENT_CURSOR, now)
        return result

    def _skip(self, event: dict) -> bool:
        if event.get("eventScopeCode") == "PUBLIC":
            return True
        return (
            event.get("actionability") == "INFORMATIONAL"
            and event["eventTypeCode"] not in self._cfg.force_track_event_types
        )

    def _entity_sweep(self, now: datetime) -> ReconcileResult:
        result = ReconcileResult()
        items = [i for state in ENTITY_SWEEP_STATES for i in self._repo.list_by_state(state)]
        for item in items:
            if item.state is State.RESOLVED_MANUAL and item.verification_mismatch:
                continue
            for arn in sorted(item.event_arns):
                record = self._repo.get_event(arn)
                if record is None:
                    continue
                entities = self._health.affected_entities(arn, item.account_id)
                self._observe(record.to_snapshot(item.account_id, entities, "RECONCILE"), now, result)
        self._repair_missing_notices({i.group_id for i in items})
        return result

    def _repair_missing_notices(self, group_ids: set[str]) -> None:
        intents = []
        for group_id in sorted(group_ids):
            group = self._repo.get_group(group_id)
            event = self._repo.get_event(group.primary_arn) if group else None
            if event is None:
                continue
            posted = {n.channel_id for n in self._repo.get_notices(group_id)}
            needed = {
                channel
                for item in self._repo.list_group_tracking(group_id)
                if not item.is_terminal
                for channel in self._router.channels_for(item.account_id, event.personas)
            }
            if needed - posted:
                intents.append(Intent(IntentKind.REFRESH, group_id))
        self._publisher.publish(intents)

    def _observe(self, snap: EventSnapshot, now: datetime, result: ReconcileResult) -> None:
        intents = observe_with_retry(snap, self._repo, self._cfg, now)
        result.observations += 1
        result.created += sum(1 for i in intents if i.kind is IntentKind.NEW)
        self._publisher.publish(intents)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_intent_queue.py tests/test_reconcile.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/aha/routing.py src/aha/adapters/intent_queue.py src/aha/reconcile.py tests/fakes.py tests/test_intent_queue.py tests/test_reconcile.py
git commit -m "feat: add reconciler with event/entity/full sweeps and intent publisher"
```

---

### Task 10: Slack 메시지 렌더링 (Notice, Thread, Modal)

**Files:**
- Create: `src/aha/slack/blocks.py`, `src/aha/slack/views.py`
- Test: `tests/test_blocks.py`, `tests/test_views.py`

**Interfaces:**
- Consumes: `EventRecord`, `TrackingItem`, `Intent`, `Entity`, `Config`, `priority`, `d_label`
- Produces (`blocks.py`): `MAX_ACCOUNT_LINES = 40`, `account_label(account_id, names: dict[str, str | None]) -> str`, `status_text(item) -> str`, `render_notice(event, items, names, cfg, today) -> tuple[str, list[dict]]`, `render_thread(intent, names) -> str | None`, 헬퍼 `plain(text)`, `mrkdwn(text)`
- Produces (`views.py`): `due_date_view(meta: dict, initial: date) -> dict` (callback `set_due_modal`, block `due`/`reason`, action `value`), `na_view(meta: dict) -> dict` (callback `na_modal`, block `reason`), `resources_view(items, entities: dict[str, list[Entity]], names) -> dict`, `MAX_RESOURCES = 100`
- Slack action_id 계약: `account_menu`(overflow, value `resolve:{tid}`|`na:{tid}`|`triage:{tid}`), `assign`, `set_due`, `resources`, `dashboard`, `triage_required`, `triage_na`. 그룹 버튼 block_id = `grp:{group_id}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_blocks.py`:
```python
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
```

`tests/test_views.py`:
```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_blocks.py tests/test_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.slack.blocks'`

- [ ] **Step 3: 구현**

`src/aha/slack/blocks.py`:
```python
from __future__ import annotations

from datetime import date

from aha.config import Config
from aha.domain.due_date import d_label, priority
from aha.domain.models import EventRecord, Intent, IntentKind, State, TrackingItem, split_tracking_id

MAX_ACCOUNT_LINES = 40  # Block Kit 메시지당 50블록 제한 내 유지
SUMMARY_LIMIT = 300
HEADER_LIMIT = 150
PRIORITY_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🟡"}


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
    return f"{account_id} ({name})" if name else account_id


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
        return header, [_header(header), _summary(event), {"type": "divider"}, *_account_blocks(items, names)]

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


def _summary(event: EventRecord) -> dict:
    description = event.description or "(설명 없음)"
    if len(description) > SUMMARY_LIMIT:
        description = description[:SUMMARY_LIMIT] + "…"
    return {"type": "section", "text": mrkdwn(description)}


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
    return {"type": "actions", "block_id": f"grp:{event.group_id}", "elements": elements}


def render_thread(intent: Intent, names: dict[str, str | None]) -> str | None:
    label = account_label(split_tracking_id(intent.tracking_id)[1], names) if intent.tracking_id else ""
    d = intent.data
    match intent.kind:
        case IntentKind.NEW:
            return f"신규 영향 계정 추가: {label}"
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
        case _:
            return None


def _action_text(d: dict, names: dict[str, str | None]) -> str:
    accounts = ", ".join(account_label(a, names) for a in d["accounts"])
    actor = f"<@{d['actor']}>"
    match d["action"]:
        case "ASSIGN":
            return f"{actor} 님이 {accounts} 담당자를 <@{d['assignee']}> 님으로 지정했습니다"
        case "SET_DUE":
            return f"{actor} 님이 {accounts} Due Date를 {d['due']}로 변경했습니다 (사유: {d['reason']})"
        case "RESOLVE_MANUAL":
            return f"{actor} 님이 {accounts} 조치 완료 처리했습니다"
        case "NOT_APPLICABLE":
            return f"{actor} 님이 {accounts} '해당 없음' 처리했습니다 (사유: {d['reason']})"
        case _:
            return f"{actor} 님이 {accounts} '조치 필요'로 판정했습니다. Due Date 추적을 시작합니다"
```

`src/aha/slack/views.py`:
```python
from __future__ import annotations

import json
from datetime import date

from aha.domain.models import Entity, TrackingItem
from aha.slack.blocks import account_label, mrkdwn, plain, status_text

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
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True},
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
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True},
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
            lines.append(f"• `{entity.value}` {entity.status}")
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
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_blocks.py tests/test_views.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/slack/blocks.py src/aha/slack/views.py tests/test_blocks.py tests/test_views.py
git commit -m "feat: render Slack notice, thread and modal views"
```

---

### Task 11: Slack Gateway, 서명 검증, Notifier

**Files:**
- Create: `src/aha/adapters/slack_client.py`, `src/aha/slack/signature.py`, `src/aha/notifier.py`
- Modify: `tests/fakes.py` (FakeSlack 추가)
- Test: `tests/test_slack_client.py`, `tests/test_signature.py`, `tests/test_notifier.py`

**Interfaces:**
- Consumes: `render_notice`, `render_thread`, `Router`, `Repository`, `Notice`
- Produces: `SlackRetryable(retry_after: int = 0)`, `SlackChannelError`, `SlackGateway(client)` with `post(channel, text, blocks=None, thread_ts=None) -> str`, `update(channel, ts, text, blocks) -> None`, `open_view(trigger_id, view) -> None`
- Produces: `verify_signature(signing_secret, timestamp: str, body: str, signature: str, now: float, tolerance: int = 300) -> bool`
- Produces: `Notifier(repo, slack, router, cfg).handle(intent, today) -> None`
- Produces (tests): `tests.fakes.FakeSlack(fail_channels=())` with `.posts`, `.updates`, `.views`

- [ ] **Step 1: FakeSlack 추가**

`tests/fakes.py` 하단에 추가:
```python
class FakeSlack:
    def __init__(self, fail_channels=()):
        from aha.adapters.slack_client import SlackChannelError

        self._error = SlackChannelError
        self.fail_channels = set(fail_channels)
        self.posts = []
        self.updates = []
        self.views = []

    def post(self, channel, text, blocks=None, thread_ts=None):
        if channel in self.fail_channels:
            raise self._error("channel_not_found")
        ts = f"1700000000.{len(self.posts) + 1:06d}"
        self.posts.append({"channel": channel, "text": text, "blocks": blocks, "thread_ts": thread_ts, "ts": ts})
        return ts

    def update(self, channel, ts, text, blocks):
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})

    def open_view(self, trigger_id, view):
        self.views.append({"trigger_id": trigger_id, "view": view})
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_slack_client.py`:
```python
import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from aha.adapters.slack_client import SlackChannelError, SlackGateway, SlackRetryable


def _error(status, error, headers=None):
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/chat.postMessage",
        req_args={},
        data={"ok": False, "error": error},
        headers=headers or {},
        status_code=status,
    )
    return SlackApiError("failed", response)


class StubClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def _call(self, name, kwargs, result):
        self.calls.append((name, kwargs))
        if self.exc:
            raise self.exc
        return result

    def chat_postMessage(self, **kwargs):
        return self._call("post", kwargs, {"ts": "1.0"})

    def chat_update(self, **kwargs):
        return self._call("update", kwargs, {"ok": True})

    def views_open(self, **kwargs):
        return self._call("views_open", kwargs, {"ok": True})


def test_post_drops_none_arguments():
    client = StubClient()
    assert SlackGateway(client).post("C1", "hi") == "1.0"
    assert client.calls == [("post", {"channel": "C1", "text": "hi"})]


def test_rate_limit_is_retryable():
    gateway = SlackGateway(StubClient(_error(429, "ratelimited", {"Retry-After": "7"})))
    with pytest.raises(SlackRetryable) as exc:
        gateway.update("C1", "1.0", "t", [])
    assert exc.value.retry_after == 7


@pytest.mark.parametrize("error", ["channel_not_found", "not_in_channel", "is_archived"])
def test_channel_errors(error):
    with pytest.raises(SlackChannelError):
        SlackGateway(StubClient(_error(200, error))).post("C1", "hi")


def test_other_errors_propagate():
    with pytest.raises(SlackApiError):
        SlackGateway(StubClient(_error(200, "invalid_blocks"))).post("C1", "hi")
```

`tests/test_signature.py`:
```python
import hashlib
import hmac

from aha.slack.signature import verify_signature

SECRET = "test-signing-secret"
NOW = 1_760_000_000.0
BODY = "payload=%7B%7D"


def _sign(ts, body):
    return "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()


def test_valid_signature():
    ts = str(int(NOW))
    assert verify_signature(SECRET, ts, BODY, _sign(ts, BODY), NOW)


def test_tampered_body_rejected():
    ts = str(int(NOW))
    assert not verify_signature(SECRET, ts, BODY + "x", _sign(ts, BODY), NOW)


def test_expired_timestamp_rejected():
    ts = str(int(NOW) - 301)
    assert not verify_signature(SECRET, ts, BODY, _sign(ts, BODY), NOW)


def test_non_numeric_timestamp_rejected():
    assert not verify_signature(SECRET, "abc", BODY, "v0=00", NOW)
```

`tests/test_notifier.py`:
```python
from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import Entity, Intent, IntentKind
from aha.domain.observe import observe
from aha.notifier import Notifier
from aha.routing import StaticRouter
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, TODAY, make_config, make_snapshot
from tests.fakes import FakeSlack, InMemoryRepository, MapRouter

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))


def _setup(accounts=(ACCOUNT,), router=None, slack=None, **overrides):
    repo = InMemoryRepository()
    intents = [i for a in accounts for i in observe(make_snapshot(account_id=a, **overrides), repo, CFG, NOW)]
    slack = slack or FakeSlack()
    notifier = Notifier(repo, slack, router or StaticRouter("C0SHADOW"), CFG)
    return repo, slack, notifier, intents


def test_new_intent_posts_notice_once():
    repo, slack, notifier, intents = _setup()
    notifier.handle(intents[0], TODAY)
    assert len(slack.posts) == 1
    assert (slack.posts[0]["channel"], slack.posts[0]["thread_ts"]) == ("C0SHADOW", None)
    notice = repo.get_notices(GID)[0]
    assert (notice.channel_id, notice.ts, notice.posted_channel_id) == ("C0SHADOW", slack.posts[0]["ts"], "C0SHADOW")


def test_second_account_updates_notice_and_threads():
    _, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    notifier.handle(intents[0], TODAY)
    notifier.handle(intents[1], TODAY)
    assert len(slack.updates) == 1
    assert slack.posts[1]["thread_ts"] == slack.posts[0]["ts"]
    assert slack.posts[1]["text"] == f"신규 영향 계정 추가: {OTHER_ACCOUNT}"


def test_intents_are_scoped_to_their_channel():
    router = MapRouter({ACCOUNT: "C1", OTHER_ACCOUNT: "C2"})
    _, slack, notifier, intents = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT), router=router)
    for intent in intents:
        notifier.handle(intent, TODAY)
    assert sorted(p["channel"] for p in slack.posts) == ["C1", "C2"]
    action = Intent(
        IntentKind.ACTION,
        GID,
        None,
        {"action": "ASSIGN", "actor": "U1", "accounts": [ACCOUNT], "assignee": "U2", "due": None, "reason": None, "channel": "C1"},
    )
    notifier.handle(action, TODAY)
    assert [u["channel"] for u in slack.updates] == ["C1"]
    assert slack.posts[-1]["channel"] == "C1"


def test_terminal_group_without_notice_is_not_posted():
    _, slack, notifier, intents = _setup(entities=(Entity("db-1", "RESOLVED"),))
    assert intents == []
    notifier.handle(Intent(IntentKind.REFRESH, GID), TODAY)
    assert slack.posts == []


def test_channel_error_falls_back_to_default_channel():
    repo, slack, notifier, intents = _setup(slack=FakeSlack(fail_channels={"C0SHADOW"}))
    notifier.handle(intents[0], TODAY)
    assert slack.posts[0]["channel"] == "C0DEFAULT"
    assert slack.posts[0]["text"].startswith("[라우팅 오류")
    notice = repo.get_notices(GID)[0]
    assert (notice.channel_id, notice.posted_channel_id) == ("C0SHADOW", "C0DEFAULT")


def test_refresh_updates_without_thread():
    _, slack, notifier, intents = _setup()
    notifier.handle(intents[0], TODAY)
    notifier.handle(Intent(IntentKind.REFRESH, GID), TODAY)
    assert len(slack.updates) == 1
    assert len(slack.posts) == 1
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_slack_client.py tests/test_signature.py tests/test_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.adapters.slack_client'`

- [ ] **Step 4: 구현**

`src/aha/adapters/slack_client.py`:
```python
from __future__ import annotations

import urllib.error

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

CHANNEL_ERRORS = frozenset({"channel_not_found", "not_in_channel", "is_archived"})


class SlackRetryable(Exception):
    def __init__(self, retry_after: int = 0) -> None:
        super().__init__(f"slack retry after {retry_after}s")
        self.retry_after = retry_after


class SlackChannelError(Exception):
    """대상 채널에 게시할 수 없음 (라우팅 설정 오류)."""


class SlackGateway:
    def __init__(self, client: WebClient) -> None:
        self._client = client

    def post(self, channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> str:
        return self._call(self._client.chat_postMessage, channel=channel, text=text, blocks=blocks, thread_ts=thread_ts)["ts"]

    def update(self, channel: str, ts: str, text: str, blocks: list) -> None:
        self._call(self._client.chat_update, channel=channel, ts=ts, text=text, blocks=blocks)

    def open_view(self, trigger_id: str, view: dict) -> None:
        self._call(self._client.views_open, trigger_id=trigger_id, view=view)

    @staticmethod
    def _call(fn, **kwargs):
        try:
            return fn(**{k: v for k, v in kwargs.items() if v is not None})
        except SlackApiError as e:
            error = e.response.get("error")
            if e.response.status_code == 429 or error == "ratelimited":
                raise SlackRetryable(int(e.response.headers.get("Retry-After", 1))) from e
            if error in CHANNEL_ERRORS:
                raise SlackChannelError(error) from e
            raise
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:
            raise SlackRetryable() from e
```

`src/aha/slack/signature.py`:
```python
from __future__ import annotations

import hashlib
import hmac

DEFAULT_TOLERANCE_SECONDS = 300


def verify_signature(
    signing_secret: str, timestamp: str, body: str, signature: str, now: float, tolerance: int = DEFAULT_TOLERANCE_SECONDS
) -> bool:
    """Slack v0 서명 검증. 실패하면 요청을 거부해야 한다(fail-closed)."""
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(now - ts) > tolerance:
        return False
    base = f"v0:{timestamp}:{body}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

`src/aha/notifier.py`:
```python
from __future__ import annotations

import logging
from datetime import date

from aha.adapters.slack_client import SlackChannelError
from aha.config import Config
from aha.domain.models import EventRecord, Intent, Notice, TrackingItem
from aha.domain.repository import Repository
from aha.routing import Router
from aha.slack.blocks import render_notice, render_thread

logger = logging.getLogger(__name__)


class Notifier:
    """Intent 1건을 받아 해당 Event Group의 채널별 Notice를 게시·수정하고 스레드에 변경을 남긴다."""

    def __init__(self, repo: Repository, slack, router: Router, cfg: Config) -> None:
        self._repo = repo
        self._slack = slack
        self._router = router
        self._cfg = cfg

    def handle(self, intent: Intent, today: date) -> None:
        group = self._repo.get_group(intent.group_id)
        event = self._repo.get_event(group.primary_arn) if group else None
        if event is None:
            logger.warning("group or event missing for intent", extra={"group_id": intent.group_id})
            return
        notices = {n.channel_id: n for n in self._repo.get_notices(intent.group_id)}
        by_channel: dict[str, list[TrackingItem]] = {}
        for item in self._repo.list_group_tracking(intent.group_id):
            for channel in self._router.channels_for(item.account_id, event.personas):
                by_channel.setdefault(channel, []).append(item)
        for channel, items in by_channel.items():
            if self._concerns(intent, channel, items):
                self._sync(intent, event, channel, items, notices.get(channel), today)

    @staticmethod
    def _concerns(intent: Intent, channel: str, items: list[TrackingItem]) -> bool:
        if intent.data.get("channel"):
            return intent.data["channel"] == channel
        if intent.tracking_id:
            return any(i.tracking_id == intent.tracking_id for i in items)
        return True

    def _sync(self, intent, event: EventRecord, channel: str, items, notice: Notice | None, today: date) -> None:
        names = {i.account_id: self._router.account_name(i.account_id) for i in items}
        text, blocks = render_notice(event, items, names, self._cfg, today)
        if notice is None:
            if all(i.is_terminal for i in items):
                return
            posted_channel, ts = self._post_new(channel, text, blocks)
            self._repo.put_notice(Notice(event.group_id, channel, ts, posted_channel))
            return
        try:
            self._slack.update(notice.posted_channel_id, notice.ts, text, blocks)
            thread = render_thread(intent, names)
            if thread:
                self._slack.post(notice.posted_channel_id, thread, thread_ts=notice.ts)
        except SlackChannelError:
            logger.warning("notice channel unavailable", extra={"channel": notice.posted_channel_id, "group_id": event.group_id})

    def _post_new(self, channel: str, text: str, blocks: list) -> tuple[str, str]:
        try:
            return channel, self._slack.post(channel, text, blocks=blocks)
        except SlackChannelError:
            fallback = self._cfg.default_channel
            if fallback == channel:
                raise
            logger.warning("routing channel unavailable, using default", extra={"channel": channel})
            return fallback, self._slack.post(fallback, f"[라우팅 오류: <#{channel}> 게시 실패] {text}", blocks=blocks)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_slack_client.py tests/test_signature.py tests/test_notifier.py -v`
Expected: 16 passed

- [ ] **Step 6: Commit**

```bash
git add src/aha/adapters/slack_client.py src/aha/slack/signature.py src/aha/notifier.py tests/fakes.py tests/test_slack_client.py tests/test_signature.py tests/test_notifier.py
git commit -m "feat: add Slack gateway, signature verification and notifier"
```

---

### Task 12: Slack Interaction 처리

**Files:**
- Create: `src/aha/slack/interactions.py`
- Test: `tests/test_interactions.py`

**Interfaces:**
- Consumes: `apply_group_action`, `ActionKind`, `InvalidAction`, `items_for_channel`, `due_date_view`, `na_view`, `resources_view`, `to_local_date`
- Produces: `InteractionService(repo, slack, router, publisher, cfg, clock: Callable[[], datetime])` with `handle(payload: dict) -> dict | None` (view_submission은 `{"response_action": "clear"}` 또는 `{"response_action": "errors", "errors": {block_id: msg}}`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_interactions.py`:
```python
import json
from datetime import date

from aha.domain.grouping import correlation_key, group_id_for
from aha.domain.models import DueDateSource, Notice, State, tracking_id_of
from aha.domain.observe import observe
from aha.routing import StaticRouter
from aha.slack.interactions import InteractionService
from tests.factories import ACCOUNT, NOW, OTHER_ACCOUNT, make_config, make_snapshot
from tests.fakes import FakeSlack, InMemoryRepository, RecordingPublisher

CFG = make_config()
GID = group_id_for(correlation_key(make_snapshot(), CFG.timezone))
TID = tracking_id_of(GID, ACCOUNT)
OTHER_TID = tracking_id_of(GID, OTHER_ACCOUNT)


def _setup(accounts=(ACCOUNT,), **overrides):
    repo = InMemoryRepository()
    for account in accounts:
        observe(make_snapshot(account_id=account, **overrides), repo, CFG, NOW)
    slack, pub = FakeSlack(), RecordingPublisher()
    service = InteractionService(repo, slack, StaticRouter("C0SHADOW"), pub, CFG, clock=lambda: NOW)
    return repo, slack, pub, service


def _block_action(action, channel="C0SHADOW"):
    return {"type": "block_actions", "user": {"id": "U1"}, "channel": {"id": channel}, "trigger_id": "T1", "actions": [action]}


def _group_action(action_id, **extra):
    return _block_action({"action_id": action_id, "block_id": f"grp:{GID}", **extra})


def _menu(value):
    return _block_action({"action_id": "account_menu", "block_id": "b1", "selected_option": {"value": value}})


def _submission(callback_id, meta, values):
    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {"callback_id": callback_id, "private_metadata": json.dumps(meta), "state": {"values": values}},
    }


def test_overflow_resolve_marks_manual_resolution():
    repo, _, pub, service = _setup()
    assert service.handle(_menu(f"resolve:{TID}")) is None
    assert repo.get_tracking(TID).state is State.RESOLVED_MANUAL
    assert (pub.intents[0].data["action"], pub.intents[0].data["channel"]) == ("RESOLVE_MANUAL", "C0SHADOW")


def test_overflow_na_opens_reason_modal():
    _, slack, _, service = _setup()
    service.handle(_menu(f"na:{TID}"))
    view = slack.views[0]["view"]
    assert view["callback_id"] == "na_modal"
    assert json.loads(view["private_metadata"]) == {"tracking_ids": [TID], "channel": "C0SHADOW"}


def test_assign_applies_to_active_items_in_channel():
    repo, _, pub, service = _setup(accounts=(ACCOUNT, OTHER_ACCOUNT))
    service.handle(_group_action("assign", selected_user="U2"))
    assert repo.get_tracking(TID).assignee == repo.get_tracking(OTHER_TID).assignee == "U2"
    assert len(pub.intents) == 1
    assert pub.intents[0].data["accounts"] == [ACCOUNT, OTHER_ACCOUNT]


def test_set_due_opens_modal_with_earliest_due():
    _, slack, _, service = _setup()
    service.handle(_group_action("set_due"))
    view = slack.views[0]["view"]
    assert view["callback_id"] == "set_due_modal"
    assert view["blocks"][0]["element"]["initial_date"] == "2026-11-30"


def _due_values(day, reason="일정 조정"):
    return {"due": {"value": {"selected_date": day}}, "reason": {"value": {"value": reason}}}


def test_set_due_submission_rejects_past_date():
    _, _, _, service = _setup()
    result = service.handle(_submission("set_due_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, _due_values("2026-10-01")))
    assert result == {"response_action": "errors", "errors": {"due": "오늘 이후 날짜를 선택하세요"}}


def test_set_due_submission_saves_manual_date():
    repo, _, _, service = _setup()
    result = service.handle(_submission("set_due_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, _due_values("2026-12-31")))
    assert result == {"response_action": "clear"}
    item = repo.get_tracking(TID)
    assert (item.due_date, item.due_date_source) == (date(2026, 12, 31), DueDateSource.MANUAL)


def test_na_submission_with_blank_reason_returns_error():
    _, _, _, service = _setup()
    values = {"reason": {"value": {"value": "   "}}}
    result = service.handle(_submission("na_modal", {"tracking_ids": [TID], "channel": "C0SHADOW"}, values))
    assert result == {"response_action": "errors", "errors": {"reason": "사유를 입력하세요"}}


def test_triage_required_promotes_triage_items():
    repo, _, _, service = _setup(actionability="ACTION_MAY_BE_REQUIRED")
    service.handle(_group_action("triage_required"))
    assert repo.get_tracking(TID).state is State.OPEN


def test_resources_opens_modal():
    _, slack, _, service = _setup()
    service.handle(_group_action("resources"))
    assert slack.views[0]["view"]["title"]["text"] == "영향 리소스"


def test_action_from_fallback_channel_maps_to_logical_channel():
    repo, _, pub, service = _setup()
    repo.put_notice(Notice(GID, "C0SHADOW", "1.0", "C0DEFAULT"))
    service.handle(_block_action({"action_id": "assign", "block_id": f"grp:{GID}", "selected_user": "U2"}, channel="C0DEFAULT"))
    assert repo.get_tracking(TID).assignee == "U2"
    assert pub.intents[0].data["channel"] == "C0SHADOW"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_interactions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.slack.interactions'`

- [ ] **Step 3: 구현**

`src/aha/slack/interactions.py`:
```python
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime

from aha.config import Config
from aha.domain.actions import ActionKind, InvalidAction, apply_group_action
from aha.domain.due_date import to_local_date
from aha.domain.models import State, TrackingItem, split_tracking_id
from aha.domain.repository import Repository
from aha.routing import Router, items_for_channel
from aha.slack.views import due_date_view, na_view, resources_view

MENU_ACTIONS = {"resolve": ActionKind.RESOLVE_MANUAL, "triage": ActionKind.TRIAGE_REQUIRED}


def _errors(block_id: str, message: str) -> dict:
    return {"response_action": "errors", "errors": {block_id: message}}


class InteractionService:
    def __init__(self, repo: Repository, slack, router: Router, publisher, cfg: Config, clock: Callable[[], datetime]) -> None:
        self._repo = repo
        self._slack = slack
        self._router = router
        self._publisher = publisher
        self._cfg = cfg
        self._clock = clock

    def handle(self, payload: dict) -> dict | None:
        kind = payload.get("type")
        if kind == "block_actions":
            self._block_action(payload)
            return None
        if kind == "view_submission":
            return self._view_submission(payload)
        return None

    # --- block_actions ---
    def _block_action(self, payload: dict) -> None:
        action = payload["actions"][0]
        action_id = action["action_id"]
        user = payload["user"]["id"]
        trigger = payload.get("trigger_id")
        if action_id == "dashboard":
            return

        if action_id == "account_menu":
            op, tracking_id = action["selected_option"]["value"].split(":", 1)
            group_id, _ = split_tracking_id(tracking_id)
            channel = self._logical_channel(group_id, payload["channel"]["id"])
            if op == "na":
                self._slack.open_view(trigger, na_view({"tracking_ids": [tracking_id], "channel": channel}))
            else:
                self._apply([tracking_id], MENU_ACTIONS[op], user, channel)
            return

        group_id = action["block_id"].removeprefix("grp:")
        channel = self._logical_channel(group_id, payload["channel"]["id"])
        if action_id == "resources":
            self._slack.open_view(trigger, self._resources(group_id, channel))
            return
        items = self._channel_items(group_id, channel, active_only=True)
        if not items:
            return
        tracking_ids = [i.tracking_id for i in items]
        triage_ids = [i.tracking_id for i in items if i.state is State.TRIAGE]
        meta = {"tracking_ids": tracking_ids, "channel": channel}

        if action_id == "assign":
            self._apply(tracking_ids, ActionKind.ASSIGN, user, channel, assignee=action["selected_user"])
        elif action_id == "triage_required":
            self._apply(triage_ids, ActionKind.TRIAGE_REQUIRED, user, channel)
        elif action_id == "triage_na":
            self._slack.open_view(trigger, na_view({**meta, "tracking_ids": triage_ids}))
        elif action_id == "set_due":
            self._slack.open_view(trigger, due_date_view(meta, min(i.due_date for i in items)))

    # --- view_submission ---
    def _view_submission(self, payload: dict) -> dict:
        view = payload["view"]
        meta = json.loads(view["private_metadata"])
        values = view["state"]["values"]
        user = payload["user"]["id"]
        try:
            if view["callback_id"] == "set_due_modal":
                due = date.fromisoformat(values["due"]["value"]["selected_date"])
                if due < to_local_date(self._clock(), self._cfg.timezone):
                    return _errors("due", "오늘 이후 날짜를 선택하세요")
                reason = values["reason"]["value"]["value"]
                self._apply(meta["tracking_ids"], ActionKind.SET_DUE, user, meta["channel"], due=due, reason=reason)
            elif view["callback_id"] == "na_modal":
                reason = values["reason"]["value"]["value"]
                self._apply(meta["tracking_ids"], ActionKind.NOT_APPLICABLE, user, meta["channel"], reason=reason)
        except InvalidAction as e:
            return _errors("reason", str(e))
        return {"response_action": "clear"}

    # --- helpers ---
    def _apply(self, tracking_ids: list[str], kind: ActionKind, actor: str, channel: str, **kwargs) -> None:
        intents = apply_group_action(self._repo, tracking_ids, kind, actor, self._clock(), channel=channel, **kwargs)
        self._publisher.publish(intents)

    def _logical_channel(self, group_id: str, channel: str) -> str:
        """대체 채널(default_channel)에 게시된 Notice의 버튼이면 원래 라우팅 채널로 되돌린다."""
        for notice in self._repo.get_notices(group_id):
            if notice.posted_channel_id == channel:
                return notice.channel_id
        return channel

    def _channel_items(self, group_id: str, channel: str, active_only: bool) -> list[TrackingItem]:
        group = self._repo.get_group(group_id)
        event = self._repo.get_event(group.primary_arn) if group else None
        if event is None:
            return []
        items = items_for_channel(self._repo.list_group_tracking(group_id), channel, self._router, event.personas)
        return [i for i in items if not i.is_terminal] if active_only else items

    def _resources(self, group_id: str, channel: str) -> dict:
        items = self._channel_items(group_id, channel, active_only=False)
        entities = {i.tracking_id: self._repo.list_entities(i.tracking_id) for i in items}
        names = {i.account_id: self._router.account_name(i.account_id) for i in items}
        return resources_view(items, entities, names)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_interactions.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/aha/slack/interactions.py tests/test_interactions.py
git commit -m "feat: handle Slack interactions for assign, due date, resolve and triage"
```

---

### Task 13: Lambda 핸들러

**Files:**
- Create: `src/aha/handlers/deps.py`, `src/aha/handlers/reconcile.py`, `src/aha/handlers/notifier.py`, `src/aha/handlers/interaction.py`
- Test: `tests/test_interaction_http.py`, `tests/test_handlers_import.py`

**Interfaces:**
- Consumes: 모든 서비스/어댑터
- Produces: 환경변수 `TABLE_NAME`, `NOTIFY_QUEUE_URL`, `CONFIG_PARAM`, `SLACK_SECRET_ARN`, `POWERTOOLS_SERVICE_NAME=aha`, `POWERTOOLS_METRICS_NAMESPACE=AHA`
- Produces: Lambda 엔트리포인트 `aha.handlers.reconcile.lambda_handler` (입력 `{"mode": "event"|"entity"|"full"}`), `aha.handlers.notifier.lambda_handler` (SQS FIFO 배치, partial failure), `aha.handlers.interaction.lambda_handler` (API GW HTTP API v2 payload)
- Produces: `aha.handlers.interaction.handle_http(event, signing_secret, service, now: float) -> dict`
- Produces: CloudWatch 메트릭 `AHA/ReconcileSuccess`, `AHA/ReconcileMissedItems` (차원 `service=aha`, `mode`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_interaction_http.py`:
```python
import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

from aha.domain.actions import InvalidAction
from aha.handlers.interaction import handle_http

SECRET = "test-signing-secret"
NOW = 1_760_000_000.0


class RecordingService:
    def __init__(self, result=None, exc=None):
        self.payloads = []
        self.result = result
        self.exc = exc

    def handle(self, payload):
        self.payloads.append(payload)
        if self.exc:
            raise self.exc
        return self.result


def _event(payload, secret=SECRET, ts=None, b64=False):
    body = urlencode({"payload": json.dumps(payload)})
    ts = ts or str(int(NOW))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "headers": {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig},
        "body": base64.b64encode(body.encode()).decode() if b64 else body,
        "isBase64Encoded": b64,
    }


def test_invalid_signature_is_rejected():
    service = RecordingService()
    resp = handle_http(_event({"type": "block_actions"}, secret="wrong"), SECRET, service, NOW)
    assert resp["statusCode"] == 401
    assert service.payloads == []


def test_valid_request_dispatches_payload():
    service = RecordingService()
    resp = handle_http(_event({"type": "block_actions"}, b64=True), SECRET, service, NOW)
    assert resp == {"statusCode": 200, "body": ""}
    assert service.payloads == [{"type": "block_actions"}]


def test_view_submission_result_is_json():
    service = RecordingService(result={"response_action": "clear"})
    resp = handle_http(_event({"type": "view_submission"}), SECRET, service, NOW)
    assert json.loads(resp["body"]) == {"response_action": "clear"}


def test_invalid_action_is_acknowledged():
    service = RecordingService(exc=InvalidAction("이미 종결된 항목입니다"))
    assert handle_http(_event({"type": "block_actions"}), SECRET, service, NOW)["statusCode"] == 200
```

`tests/test_handlers_import.py`:
```python
import importlib

import pytest


@pytest.mark.parametrize("module", ["aha.handlers.reconcile", "aha.handlers.notifier", "aha.handlers.interaction"])
def test_handler_modules_import_without_aws_calls(module, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "AHA")
    assert callable(importlib.import_module(module).lambda_handler)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_interaction_http.py tests/test_handlers_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aha.handlers.interaction'`

- [ ] **Step 3: 구현**

`src/aha/handlers/deps.py`:
```python
"""Lambda 실행 환경용 의존성. 모두 지연 생성되어 import 시점에는 AWS를 호출하지 않는다.

설정은 콜드 스타트마다 SSM에서 다시 읽는다.
"""
from __future__ import annotations

import json
import os
from functools import cache

import boto3
from slack_sdk import WebClient

from aha.adapters.health_client import HealthClient
from aha.adapters.intent_queue import SqsIntentPublisher
from aha.adapters.repo_dynamodb import DynamoRepository
from aha.adapters.slack_client import SlackGateway
from aha.config import Config, load_config
from aha.routing import StaticRouter

SLACK_TIMEOUT_SECONDS = 5


@cache
def config() -> Config:
    return load_config(boto3.client("ssm"), os.environ["CONFIG_PARAM"])


@cache
def repository() -> DynamoRepository:
    return DynamoRepository(boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]))


@cache
def publisher() -> SqsIntentPublisher:
    return SqsIntentPublisher(boto3.client("sqs"), os.environ["NOTIFY_QUEUE_URL"])


@cache
def health() -> HealthClient:
    return HealthClient()


@cache
def slack_secret() -> dict:
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SLACK_SECRET_ARN"])
    return json.loads(secret["SecretString"])


@cache
def slack() -> SlackGateway:
    return SlackGateway(WebClient(token=slack_secret()["bot_token"], timeout=SLACK_TIMEOUT_SECONDS))


@cache
def router() -> StaticRouter:
    cfg = config()
    return StaticRouter(cfg.shadow_channel or cfg.default_channel)
```

`src/aha/handlers/reconcile.py`:
```python
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit

from aha.handlers import deps
from aha.reconcile import Reconciler

logger = Logger()
metrics = Metrics()


@metrics.log_metrics
@logger.inject_lambda_context
def lambda_handler(event, context):
    mode = event["mode"]
    reconciler = Reconciler(deps.health(), deps.repository(), deps.publisher(), deps.router(), deps.config())
    result = reconciler.run(mode, datetime.now(UTC))
    metrics.add_dimension(name="mode", value=mode)
    metrics.add_metric(name="ReconcileSuccess", unit=MetricUnit.Count, value=1)
    if mode == "full":
        metrics.add_metric(name="ReconcileMissedItems", unit=MetricUnit.Count, value=result.created)
    logger.info("reconcile finished", extra={"mode": mode, **asdict(result)})
    return asdict(result)
```

`src/aha/handlers/notifier.py`:
```python
from __future__ import annotations

from datetime import UTC, datetime

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.batch import SqsFifoPartialProcessor, process_partial_response
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord

from aha.domain.due_date import to_local_date
from aha.domain.models import Intent
from aha.handlers import deps
from aha.notifier import Notifier

logger = Logger()
processor = SqsFifoPartialProcessor()


def record_handler(record: SQSRecord) -> None:
    intent = Intent.from_json(record.body)
    cfg = deps.config()
    logger.append_keys(group_id=intent.group_id, tracking_id=intent.tracking_id, kind=intent.kind.value)
    Notifier(deps.repository(), deps.slack(), deps.router(), cfg).handle(intent, to_local_date(datetime.now(UTC), cfg.timezone))


@logger.inject_lambda_context
def lambda_handler(event, context):
    # 실패한 메시지와 같은 그룹의 후속 메시지는 재시도로 돌려 순서를 보장한다.
    return process_partial_response(event=event, record_handler=record_handler, processor=processor, context=context)
```

`src/aha/handlers/interaction.py`:
```python
from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs

from aws_lambda_powertools import Logger

from aha.domain.actions import InvalidAction
from aha.handlers import deps
from aha.slack.interactions import InteractionService
from aha.slack.signature import verify_signature

logger = Logger()


def handle_http(event: dict, signing_secret: str, service, now: float) -> dict:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not verify_signature(
        signing_secret,
        headers.get("x-slack-request-timestamp", ""),
        body,
        headers.get("x-slack-signature", ""),
        now,
    ):
        return {"statusCode": 401, "body": "invalid signature"}

    form = parse_qs(body)
    if "payload" not in form:
        return {"statusCode": 400, "body": "missing payload"}
    try:
        result = service.handle(json.loads(form["payload"][0]))
    except InvalidAction as e:
        logger.info("invalid action", extra={"reason": str(e)})
        result = None
    if result is None:
        return {"statusCode": 200, "body": ""}
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result, ensure_ascii=False),
    }


@logger.inject_lambda_context
def lambda_handler(event, context):
    service = InteractionService(
        deps.repository(), deps.slack(), deps.router(), deps.publisher(), deps.config(), clock=lambda: datetime.now(UTC)
    )
    return handle_http(event, deps.slack_secret()["signing_secret"], service, time.time())
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `pytest -q`
Expected: 전체 통과 (`0 failed`)

- [ ] **Step 5: Commit**

```bash
git add src/aha/handlers tests/test_interaction_http.py tests/test_handlers_import.py
git commit -m "feat: add Lambda handlers for reconcile, notifier and Slack interactions"
```

---

### Task 14: 빌드 스크립트, Terraform core 모듈, dev 환경, 런북

**Files:**
- Create: `scripts/build_lambda.sh`
- Create: `infra/modules/core/versions.tf`, `variables.tf`, `main.tf`, `iam.tf`, `lambda.tf`, `api.tf`, `alarms.tf`, `outputs.tf`
- Create: `infra/envs/dev/main.tf`, `variables.tf`, `outputs.tf`, `config.yaml`
- Create: `slack/manifest.yaml`, `README.md`

**Interfaces:**
- Consumes: Task 13의 핸들러 경로와 환경변수 이름
- Produces: `build/lambda.zip`, Terraform outputs `interaction_url`, `notify_queue_url`, `table_name`

- [ ] **Step 1: Lambda 빌드 스크립트**

`scripts/build_lambda.sh`:
```bash
#!/usr/bin/env bash
# arm64 / Python 3.12 Lambda용 단일 zip을 만든다. 모든 핸들러가 같은 zip을 사용한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build"
PKG="$OUT/pkg"

rm -rf "$PKG" "$OUT/lambda.zip"
mkdir -p "$PKG"

python3 -m pip install --quiet --target "$PKG" \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all: \
  "aws-lambda-powertools>=3.0" "slack_sdk>=3.33" "PyYAML>=6.0"

cp -R "$ROOT/src/aha" "$PKG/"
find "$PKG" -name "__pycache__" -type d -prune -exec rm -rf {} +
(cd "$PKG" && zip -qr "$OUT/lambda.zip" .)
echo "built $OUT/lambda.zip"
```

Run:
```bash
chmod +x scripts/build_lambda.sh && ./scripts/build_lambda.sh && unzip -l build/lambda.zip | grep -c "aha/handlers/"
```
Expected: `built .../build/lambda.zip` 출력 후 숫자 `5` 이상 (`__init__.py`, `deps.py`, `reconcile.py`, `notifier.py`, `interaction.py`)

- [ ] **Step 2: Terraform core 모듈 작성**

`infra/modules/core/versions.tf`:
```hcl
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}
```

`infra/modules/core/variables.tf`:
```hcl
variable "name_prefix" {
  description = "리소스 이름 접두사 (예: aha-dev)"
  type        = string
}

variable "lambda_zip_path" {
  description = "scripts/build_lambda.sh 산출물 경로"
  type        = string
}

variable "config_yaml" {
  description = "SSM에 저장할 애플리케이션 설정 YAML"
  type        = string
}

variable "slack_secret_arn" {
  description = "bot_token, signing_secret을 담은 Secrets Manager 시크릿 ARN (Terraform 외부에서 생성)"
  type        = string
}

variable "alarm_email" {
  description = "운영 알람 수신 이메일 (Slack과 별도 경로)"
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "tags" {
  type    = map(string)
  default = {}
}
```

`infra/modules/core/main.tf`:
```hcl
locals {
  functions = {
    reconcile = {
      handler = "aha.handlers.reconcile.lambda_handler"
      timeout = 900
      memory  = 512
    }
    notifier = {
      handler = "aha.handlers.notifier.lambda_handler"
      timeout = 60
      memory  = 256
    }
    interaction = {
      handler = "aha.handlers.interaction.lambda_handler"
      timeout = 10
      memory  = 256
    }
  }

  common_env = {
    TABLE_NAME                   = aws_dynamodb_table.state.name
    NOTIFY_QUEUE_URL             = aws_sqs_queue.notify.url
    CONFIG_PARAM                 = aws_ssm_parameter.config.name
    SLACK_SECRET_ARN             = var.slack_secret_arn
    POWERTOOLS_SERVICE_NAME      = "aha"
    POWERTOOLS_METRICS_NAMESPACE = "AHA"
  }
}

resource "aws_dynamodb_table" "state" {
  name                        = "${var.name_prefix}-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "gsi1pk"
    type = "S"
  }
  attribute {
    name = "gsi1sk"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi-state"
    hash_key        = "gsi1pk"
    range_key       = "gsi1sk"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}

resource "aws_sqs_queue" "notify_dlq" {
  name                      = "${var.name_prefix}-notify-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "notify" {
  name                       = "${var.name_prefix}-notify.fifo"
  fifo_queue                 = true
  visibility_timeout_seconds = 360
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notify_dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

resource "aws_ssm_parameter" "config" {
  name  = "/${var.name_prefix}/config"
  type  = "String"
  value = var.config_yaml
  tags  = var.tags
}
```

`infra/modules/core/iam.tf`:
```hcl
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "common" {
  statement {
    sid = "StateTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.state.arn, "${aws_dynamodb_table.state.arn}/index/*"]
  }
  statement {
    sid       = "Config"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.config.arn]
  }
}

data "aws_iam_policy_document" "reconcile" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid = "HealthOrganizationRead"
    actions = [
      "health:DescribeEventsForOrganization",
      "health:DescribeEventDetailsForOrganization",
      "health:DescribeAffectedAccountsForOrganization",
      "health:DescribeAffectedEntitiesForOrganization",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "PublishIntents"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.notify.arn]
  }
}

data "aws_iam_policy_document" "notifier" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid = "ConsumeIntents"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.notify.arn]
  }
  statement {
    sid       = "SlackSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.slack_secret_arn]
  }
}

data "aws_iam_policy_document" "interaction" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid       = "SlackSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.slack_secret_arn]
  }
  statement {
    sid       = "PublishIntents"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.notify.arn]
  }
}

locals {
  function_policies = {
    reconcile   = data.aws_iam_policy_document.reconcile.json
    notifier    = data.aws_iam_policy_document.notifier.json
    interaction = data.aws_iam_policy_document.interaction.json
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${var.name_prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "basic" {
  for_each   = local.functions
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "${var.name_prefix}-${each.key}"
  role     = aws_iam_role.fn[each.key].id
  policy   = local.function_policies[each.key]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["reconcile"].arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.name_prefix}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
```

`infra/modules/core/lambda.tf`:
```hcl
locals {
  reconcile_schedules = {
    event  = "rate(1 hour)"
    entity = "rate(6 hours)"
    full   = "cron(0 3 ? * SUN *)"
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = each.value.handler
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  # sweep 동시 실행으로 Health API 스로틀링이 나지 않도록 reconcile은 1개로 제한한다.
  reserved_concurrent_executions = each.key == "reconcile" ? 1 : -1

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.fn]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "notify" {
  event_source_arn        = aws_sqs_queue.notify.arn
  function_name           = aws_lambda_function.fn["notifier"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_scheduler_schedule" "reconcile" {
  for_each                     = local.reconcile_schedules
  name                         = "${var.name_prefix}-reconcile-${each.key}"
  schedule_expression          = each.value
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn["reconcile"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = each.key })

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
```

`infra/modules/core/api.tf`:
```hcl
resource "aws_apigatewayv2_api" "slack" {
  name          = "${var.name_prefix}-slack"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "interaction" {
  api_id                 = aws_apigatewayv2_api.slack.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["interaction"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "interaction" {
  api_id    = aws_apigatewayv2_api.slack.id
  route_key = "POST /slack/interactions"
  target    = "integrations/${aws_apigatewayv2_integration.interaction.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.slack.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  tags = var.tags
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowSlackApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["interaction"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack.execution_arn}/*/*/slack/interactions"
}
```

`infra/modules/core/alarms.tf`:
```hcl
# CloudWatch 알람은 AWS 관리형 키(alias/aws/sns)로 암호화된 토픽에 게시할 수 없다.
# 암호화가 필요하면 cloudwatch.amazonaws.com에 kms:Decrypt/GenerateDataKey를 허용한 CMK를 지정한다.
resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "notify_dlq" {
  alarm_name          = "${var.name_prefix}-notify-dlq-not-empty"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.notify_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.functions
  alarm_name          = "${var.name_prefix}-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# event sweep이 3시간 연속 성공하지 못하면 알린다.
resource "aws_cloudwatch_metric_alarm" "reconcile_heartbeat" {
  alarm_name          = "${var.name_prefix}-reconcile-heartbeat"
  namespace           = "AHA"
  metric_name         = "ReconcileSuccess"
  dimensions          = { service = "aha", mode = "event" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# full resync에서 새 항목이 발견되면 정기 sweep이 놓친 것이다.
resource "aws_cloudwatch_metric_alarm" "reconcile_missed" {
  alarm_name          = "${var.name_prefix}-reconcile-missed-items"
  namespace           = "AHA"
  metric_name         = "ReconcileMissedItems"
  dimensions          = { service = "aha", mode = "full" }
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
```

`infra/modules/core/outputs.tf`:
```hcl
output "interaction_url" {
  description = "Slack App Interactivity Request URL"
  value       = "${aws_apigatewayv2_api.slack.api_endpoint}/slack/interactions"
}

output "notify_queue_url" {
  value = aws_sqs_queue.notify.url
}

output "table_name" {
  value = aws_dynamodb_table.state.name
}
```

- [ ] **Step 3: dev 환경 작성**

`infra/envs/dev/main.tf`:
```hcl
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
  # 원격 상태는 조직 표준 백엔드를 사용한다. 예:
  # backend "s3" {}
}

provider "aws" {
  region = "ap-northeast-2"
}

module "core" {
  source           = "../../modules/core"
  name_prefix      = "aha-dev"
  lambda_zip_path  = "${path.root}/../../../build/lambda.zip"
  config_yaml      = file("${path.module}/config.yaml")
  slack_secret_arn = var.slack_secret_arn
  alarm_email      = var.alarm_email
  tags = {
    Project     = "aws-org-health-aware"
    Environment = "dev"
  }
}
```

`infra/envs/dev/variables.tf`:
```hcl
variable "slack_secret_arn" {
  type = string
}

variable "alarm_email" {
  type = string
}
```

`infra/envs/dev/outputs.tf`:
```hcl
output "interaction_url" {
  value = module.core.interaction_url
}
```

`infra/envs/dev/config.yaml` (채널 ID는 P0에서 확정한 값으로 교체한다 — spec Q5):
```yaml
timezone: Asia/Seoul
default_channel: C0000000000
shadow_channel: C0000000000
sla_days:
  issue: 3
  SECURITY: 7
  default: 30
force_track_event_types: []
suppress_event_types: []
health_dashboard_url: https://health.aws.amazon.com/health/home
```

- [ ] **Step 4: Terraform 검증**

Run:
```bash
terraform fmt -check -recursive infra
terraform -chdir=infra/envs/dev init -backend=false
terraform -chdir=infra/envs/dev validate
```
Expected: `fmt` 출력 없음(종료 코드 0), `Success! The configuration is valid.`

- [ ] **Step 5: Slack manifest와 README 런북 작성**

`slack/manifest.yaml`:
```yaml
display_information:
  name: AWS Health Action Tracker
  description: AWS Health Action Required 이벤트 추적
features:
  bot_user:
    display_name: aws-health
    always_online: true
oauth_config:
  scopes:
    bot:
      - chat:write
      - chat:write.public
      - users:read
settings:
  interactivity:
    is_enabled: true
    # terraform output interaction_url 값으로 설정한다.
    request_url: https://example.invalid/slack/interactions
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

`README.md`:
````markdown
# aws-org-health-aware

AWS Health Organizational view에서 조치가 필요한 이벤트(ACTION_REQUIRED / ACTION_MAY_BE_REQUIRED)를 계정 단위로 추적하고 Slack으로 알린다.

- 설계: `docs/specs/2026-10-12-health-action-required-slack-design.md`
- P1 구현 계획: `docs/plans/2026-10-12-health-ar-slack-p1-mvp.md`

## 개발

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## 배포 (dev, Health 위임 관리자 계정)

> 모든 단계는 Health 위임 관리자 계정 자격 증명으로 실행한다. 관리 계정에 배포하지 않는다.
> `terraform apply`는 변경 내역(`plan`)을 검토하고 승인한 뒤에만 실행한다.

1. Slack App 생성: `slack/manifest.yaml`로 앱을 만들고 워크스페이스에 설치한다. Bot token(`xoxb-`)과 Signing secret을 확인한다.
2. 시크릿 생성 (값은 셸 히스토리에 남지 않도록 파일로 전달):
   ```bash
   aws secretsmanager create-secret --name aha/dev/slack --secret-string file://slack-secret.json
   # slack-secret.json: {"bot_token": "xoxb-...", "signing_secret": "..."}
   ```
3. `infra/envs/dev/config.yaml`의 `default_channel`, `shadow_channel`을 실제 채널 ID로 바꾼다.
4. 빌드와 계획:
   ```bash
   ./scripts/build_lambda.sh
   terraform -chdir=infra/envs/dev init
   terraform -chdir=infra/envs/dev plan -var slack_secret_arn=<ARN> -var alarm_email=<EMAIL> -out tfplan
   ```
5. 계획 검토 후 적용: `terraform -chdir=infra/envs/dev apply tfplan`
6. `terraform output interaction_url` 값을 Slack App의 Interactivity Request URL에 설정한다.
7. SNS 구독 확인 메일을 승인한다.

## 섀도 운영 (P1, 2주)

- 모든 알림은 `shadow_channel` 하나로 간다.
- 매일 Health 메일 수신 내역과 섀도 채널을 대조해 누락·중복을 기록한다.
  - 누락: 메일에는 있으나 채널에 없는 ACTION_REQUIRED 이벤트
  - 중복: 같은 이벤트에 대해 채널에 메시지가 2개 이상
- 첫 주에 실제 이벤트 페이로드에서 `actionability`, `personas` 값이 채워지는지 확인한다 (spec Q1).
- 완료 기준: 2주간 누락 0건, 중복 메시지 0건.

## 운영 점검

| 알람 | 의미 | 조치 |
|---|---|---|
| `*-reconcile-heartbeat` | event sweep 3시간 이상 실패 | reconcile Lambda 로그 확인 (Health API 권한/스로틀링) |
| `*-reconcile-missed-items` | 주간 full resync에서 누락 발견 | event sweep 커서·필터 점검 |
| `*-notify-dlq-not-empty` | Slack 게시 5회 실패 | DLQ 메시지 확인 후 원인 해결, SQS 콘솔의 DLQ redrive로 재처리 |
| `*-<fn>-errors` | Lambda 오류 | 해당 함수 로그 확인 |
````

- [ ] **Step 6: 전체 테스트 재확인 및 Commit**

Run: `pytest -q`
Expected: `0 failed`

```bash
git add scripts infra slack README.md
git commit -m "feat: add Lambda build, Terraform core module, dev env and runbook"
```

---

## Spec Coverage (P1)

| Spec 항목 | Task |
|---|---|
| 3.1–3.2 분류·Fallback·억제 안전 규칙 | 2 |
| 3.3 우선순위, 3.4 Due Date·SLA·수동 유지 | 3, 5 |
| 5.3 Reconcile (event/entity/full, 커서, 겹침, PUBLIC 제외) | 8, 9 |
| 6.2 그룹 연결, 6.3 상태 머신, 72h 불일치, 6.4 observe | 4, 5 |
| 7.1–7.2 저장소 | 7 |
| 8 라우팅 | 9 (`StaticRouter`, P2에서 태그 라우팅) |
| 9.2–9.4 Notice·스레드·인터랙션 | 10, 11, 12 |
| 9.7 FIFO 직렬화, REFRESH 복구, 대체 채널 | 9, 11 |
| 10 알람(DLQ, heartbeat, missed, errors) | 13, 14 |
| 11 최소 권한, 서명 검증 fail-closed, 암호화, PITR | 11, 13, 14 |
| 13 테스트 전략 (unit, moto, 시나리오) | 전 Task |
| 14 P1 섀도 운영 | 14 (README) |
| 9.5 리마인더, 9.6 다이제스트, 8.2 태그 라우팅, TRIAGE 자동 승격 | P2 Plan |
| 5.1–5.2 EventBridge 수집, 7.3 dedup | P3 Plan |
