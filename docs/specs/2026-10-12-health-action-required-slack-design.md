# AWS Health Action Required → Slack 추적 시스템 개발 명세서

- 작성일: 2026-10-12
- 상태: Draft (리뷰 대기)
- 범위: AWS Organizations 전체 계정의 AWS Health 이벤트 중 조치가 필요한 항목을 식별하고, Due Date 이전에 담당자가 인지·조치하도록 Slack 기반으로 알림·추적한다.

---

## 1. 배경 및 목표

### 1.1 현재 문제
- 다수 AWS 계정에서 Health 메일이 대량 발송되고, 동일 이벤트에 대한 반복 메일이 존재한다.
- 메일만으로는 영향 리소스 ID, open/closed 상태, 리소스별 조치 여부(PENDING/RESOLVED)를 알 수 없다.
- AWS는 Reminder/Escalation 주기를 보장하지 않는다. 메일 수신에 의존하면 Due Date를 놓칠 수 있다.

### 1.2 목표
| # | 목표 | 측정 기준 (KPI) |
|---|---|---|
| G1 | 조치 필요 이벤트 **누락 0건** | 주간 전체 재동기화 결과와 추적 항목 간 차이 = 0 |
| G2 | 동일 이벤트 **중복 알림 제거** | (이벤트 그룹, 채널)당 Slack 메시지 1개 |
| G3 | Due Date 기준 **능동 리마인드** | 마일스톤 리마인더 발송 누락 0건 |
| G4 | 조치 완료의 **객관적 판정** | 동적 상태 지원 이벤트는 Health API 리소스 상태로 자동 완료 |
| G5 | 신속한 인지 | 신규 이벤트 Slack 게시 지연 p95: 실시간 경로 5분, 대사 경로 75분 이내 |
| G6 | 담당 명확화 | D-7 시점 OPEN 항목의 담당자 지정률 100% |

### 1.3 범위 제외 (Non-Goals)
- Jira/ServiceNow 연동 (SMC 커넥터는 2027-03 deprecated 예정, 본 시스템은 Slack 전용)
- INFORMATIONAL 이벤트 알림
- 기존 Health 메일(User Notifications) 중단: 메일은 백업 채널로 유지한다.
- 이벤트 본문 자동 번역
- AWS GovCloud(US)·중국 파티션 수집 (해당 리전 이벤트는 commercial 파티션에서 관측되는 경우에만 '수동 완료 필요'로 처리)

---

## 2. 전제 조건

| 항목 | 내용 |
|---|---|
| Health Organizational view | 활성화됨 |
| Health Delegated Administrator | 활성화됨. **본 시스템은 위임 관리자 계정(이하 '수집 계정')에 배포**한다. 관리 계정에는 리소스를 배포하지 않는다. |
| Organizations 조회 권한 | 수집 계정이 계정 태그를 조회할 수 있도록 Organizations **resource-based delegation policy**로 `organizations:ListAccounts`, `DescribeAccount`, `ListTagsForResource`, `ListParents`를 위임한다. (Health 위임 관리자 권한만으로는 Organizations API를 호출할 수 없음) |
| 홈 리전 | 기본값 `ap-northeast-2`. 상태 저장소·Lambda·Slack 연동이 위치한다. |
| 수집 리전 | 운영 리전 전체 + `us-east-1`(글로벌 이벤트) + `us-west-2`(글로벌 이벤트 백업, `backupEvent: true`) |
| Slack | 사내 Slack Workspace에 전용 Slack App(Bot) 설치 권한 |
| IaC / 런타임 | Terraform, Python 3.12 Lambda (boto3, AWS Lambda Powertools) |

---

## 3. 이벤트 분류 정책 (Action 목록 분류 기준)

### 3.1 Tier 분류
| Tier | 조건 | 처리 |
|---|---|---|
| **Tier 1: 필수 추적** | `actionability = ACTION_REQUIRED` 또는 `eventTypeCode ∈ force_track_event_types`(설정) | Due Date 추적, 마일스톤 리마인더, 에스컬레이션, 다이제스트 대상. **억제(suppress) 불가** |
| **Tier 2: 검토 큐** | `actionability = ACTION_MAY_BE_REQUIRED` | 1회 알림 + 담당자 판정(조치 필요 / 해당 없음). '조치 필요' 판정 시 Tier 1로 승격 |
| 제외 | `actionability = INFORMATIONAL` 또는 Tier 2 중 `eventTypeCode ∈ suppress_event_types`(설정) | 저장·알림 안 함 |

> 안전 규칙: `suppress_event_types`는 Tier 2에만 적용된다. ACTION_REQUIRED 이벤트는 어떤 설정으로도 제외되지 않는다.

### 3.2 actionability 누락 시 Fallback
과거 이벤트나 필드가 비어 있는 이벤트는 아래 규칙으로 분류한다. 분류 결과에는 `classificationSource = FALLBACK`을 기록한다.

| 조건 | 분류 |
|---|---|
| `eventTypeCategory = scheduledChange` | Tier 1 |
| `eventTypeCode`에 `PLANNED_LIFECYCLE_EVENT`, `_SCHEDULED`, `RETIREMENT` 포함 | Tier 1 |
| `eventTypeCategory = accountNotification` | Tier 2 |
| `eventTypeCategory = issue` 이고 영향 엔티티 존재 | Tier 2 |
| 그 외 (`investigation` 포함) | 제외 |

### 3.3 우선순위 (Tier 1 내부)
| 우선순위 | 조건 | 표시 |
|---|---|---|
| P1 | `personas`에 `SECURITY` 포함, 또는 남은 일수 ≤ 14 | 🔴 |
| P2 | 남은 일수 ≤ 30 | 🟠 |
| P3 | 그 외 | 🟡 |

우선순위는 매일 리마인더 작업에서 다시 계산한다.

### 3.4 Due Date 산정
| 이벤트 유형 | Due Date | `dueDateSource` |
|---|---|---|
| Planned Lifecycle Event, `scheduledChange` | `startTime` (없으면 `endTime`) | `AWS` |
| 그 외 Tier 1, `startTime`이 미래 | `startTime` | `AWS` |
| 그 외 Tier 1, 날짜 없음 또는 과거 | 최초 감지일 + 기본 SLA | `DEFAULT_SLA` |
| 담당자가 Slack에서 수정 | 입력값 | `MANUAL` |

기본 SLA (설정값): `issue` 3일, `SECURITY` persona 7일, 그 외 30일. 여러 조건에 해당하면 가장 짧은 값을 적용한다.

AWS가 `startTime`을 변경하면 `dueDateSource = AWS`인 경우에만 자동으로 갱신하고, 스레드에 "Due Date 변경: A → B"를 게시한다. `MANUAL`인 경우에는 덮어쓰지 않고 변경 사실만 스레드에 알린다.

---

## 4. 아키텍처

### 4.1 구성도
```
[수집 계정 (Health 위임 관리자)]

 각 수집 리전
   EventBridge Rule ── source=aws.health, detail.actionability ∈ {ACTION_REQUIRED, ACTION_MAY_BE_REQUIRED}
        │ (cross-region target, IAM role)
        ▼
 홈 리전
   Custom Event Bus "aha-health" ─► SQS ingest (+DLQ) ─► [Ingest Lambda] ─┐
                                                                         │
   Scheduler rate(1h)      ─► [Reconcile Lambda: event sweep]  ──────────┤   Health Org API
   Scheduler rate(6h)      ─► [Reconcile Lambda: entity sweep] ──────────┤   (global endpoint)
   Scheduler 주 1회         ─► [Reconcile Lambda: full resync]  ──────────┤
                                                                         ▼
                                                  Domain Core (공통 라이브러리)
                                                         │        │
                                              DynamoDB aha-state  │ 알림 Intent
                                              DynamoDB aha-dedup  ▼
                                                     SQS FIFO notify (+DLQ) ─► [Notifier Lambda] ─► Slack Web API
   Scheduler 매일 09:00 KST   ─► [Reminder Lambda] ─► SQS FIFO notify
   Scheduler 월 09:00 KST     ─► [Digest Lambda]   ─► SQS FIFO notify

   Slack Interactivity ─► API Gateway HTTP API ─► [Interaction Lambda] ─► Domain Core
   Organizations (delegation policy) ─► Routing Resolver (계정 태그 캐시)
   Secrets Manager: Slack bot token, signing secret
```

### 4.2 설계 원칙
1. **추적 단위 = (eventArn, affectedAccount)**: 상태, Due Date, 담당자, 완료 판정을 계정 단위로 관리한다. 이를 Tracking Item이라 한다.
2. **알림 단위 = (Event Group, Slack 채널)**: 같은 이벤트가 한 팀의 여러 계정에 영향을 주면 **채널당 메시지 1개**에 계정별 상태를 나열한다. 계정 수만큼 메시지가 생기는 노이즈를 막기 위함이다. 이 메시지를 Notice라 한다.
3. **Health API가 기준 데이터(source of truth)**: EventBridge는 빠른 감지 경로이고, 완료 판정과 누락 보정은 Reconcile이 담당한다.
4. **단일 도메인 로직**: Ingest, Reconcile, Interaction은 모두 Domain Core의 `observe()`와 `apply_action()`을 호출한다. 처리 경로가 달라도 결과는 같다.
5. **멱등성**: 모든 쓰기는 조건부 쓰기(낙관적 잠금, `version` 속성)로 한다. 같은 입력을 여러 번 처리해도 결과가 같아야 한다.
6. **실패 시 알림이 사라지지 않도록**: 시스템 장애 알람은 Slack이 아닌 별도 경로(SNS → 이메일)로도 받는다.

### 4.3 컴포넌트 책임
| 컴포넌트 | 책임 | 입력 → 출력 |
|---|---|---|
| Regional Forwarder (Terraform 모듈) | 리전별 Health 이벤트를 홈 리전 버스로 전달 | aws.health 이벤트 → Custom Bus |
| Ingest Lambda | 페이로드 검증, 중복 제거, 스냅샷 변환 후 `observe()` 호출 | SQS 메시지 → 상태 변경 + 알림 Intent |
| Reconcile Lambda | Health Org API 조회 후 `observe()` 호출 (event sweep / entity sweep / full resync) | API 응답 → 상태 변경 + 알림 Intent |
| Domain Core | 분류, Due Date 산정, 상태 전이, Event Group 연결, Intent 생성 | 스냅샷/액션 → 상태 + Intent |
| Routing Resolver | 계정 → 채널/멘션 대상 결정 | accountId, personas → Route |
| Notifier Lambda | Intent를 Block Kit으로 렌더링 후 Slack 게시/수정, `ts` 저장 | Intent → Slack 메시지 |
| Reminder Lambda | 매일 남은 일수 계산, 마일스톤·에스컬레이션·기한 경과 Intent 생성 | 상태 → Intent |
| Digest Lambda | 주간 팀별/전사 요약 생성 | 상태 → Intent |
| Interaction Lambda | Slack 서명 검증, 버튼·모달 처리 후 `apply_action()` 호출 | Slack payload → 상태 변경 + Intent |

---

## 5. 데이터 수집

### 5.1 EventBridge Rule (리전별)
```json
{
  "source": ["aws.health"],
  "detail-type": ["AWS Health Event"],
  "detail": {
    "actionability": ["ACTION_REQUIRED", "ACTION_MAY_BE_REQUIRED"]
  }
}
```
- 홈 리전의 Custom Bus `aha-health`로 cross-region 전달한다.
- actionability가 비어 있는 이벤트는 이 Rule에서 걸러진다. 이런 이벤트는 Reconcile의 Fallback 분류(3.2)로 처리한다.
- dev 환경에서만 `source = custom.aha.test`도 허용해 합성 이벤트를 주입할 수 있게 한다. (`aws.health` source로는 PutEvents를 할 수 없음)

### 5.2 Ingest 처리 순서
1. EventBridge 엔벨로프에서 `detail`을 추출하고 스키마를 검증한다. 검증에 실패하면 DLQ로 보낸다.
2. **메시지 중복 제거**: `aha-dedup`에 `communicationId#affectedAccount#page`를 조건부 Put(`attribute_not_exists`, TTL 30일)한다. 이미 있으면 건너뛴다. backupEvent, at-least-once 재전송, 페이지 분할 메시지를 모두 이 키로 흡수한다.
3. `EventSnapshot`으로 변환한다 (6.1). 페이지가 분할된 경우 각 페이지의 `affectedEntities`를 누적 upsert한다.
4. `observe(snapshot)`을 호출한다.
5. SQS partial batch response로 실패 건만 재시도한다.

### 5.3 Reconcile 처리
| 모드 | 주기 | 조회 범위 | 목적 |
|---|---|---|---|
| event sweep | 1시간 | `DescribeEventsForOrganization` (`lastUpdatedTime` ≥ 직전 성공 시각 − 2시간 겹침) | 신규·변경 이벤트 보정 |
| entity sweep | 6시간 | 비종결 Tracking Item 전체 + RESOLVED_AUTO(해결 후 30일, 재발 감시) + RESOLVED_MANUAL(불일치 검증 창 72h+12h, 미플래그 항목)에 대해 `DescribeAffectedEntitiesForOrganization` | PENDING→RESOLVED 전환 반영 (엔티티 변경은 이벤트 `lastUpdatedTime`에 반영되지 않을 수 있음) |
| full resync | 주 1회 | `eventStatusCodes = open, upcoming` 전체 | 누락 검증 (KPI G1). 새로 생성된 항목 수를 `ReconcileMissedItems` 메트릭으로 발행 |

이벤트별 보조 호출:
- `DescribeEventDetailsForOrganization`: 이벤트 설명(`latestDescription`)을 가져온다. 설명 해시가 바뀐 경우에만 '내용 갱신' Intent를 만든다.
- `DescribeAffectedAccountsForOrganization`: 영향 계정 목록을 가져온다.

API 운용:
- Health global endpoint를 사용하고 boto3 `adaptive` retry 모드를 쓴다. 스로틀링 시 지수 백오프(jitter)로 재시도한다.
- 직전 성공 시각은 `aha-state`의 `CURSOR#reconcile` 항목에 저장한다. 실패한 실행은 커서를 전진시키지 않는다.

---

## 6. 도메인 모델

### 6.1 EventSnapshot (정규화된 입력)
```python
@dataclass(frozen=True)
class EventSnapshot:
    event_arn: str
    account_id: str              # affectedAccount
    service: str
    event_type_code: str
    category: str                # issue | accountNotification | scheduledChange | investigation
    event_region: str
    status_code: str             # open | upcoming | closed
    start_time: datetime | None
    end_time: datetime | None
    last_updated_time: datetime
    actionability: str | None
    personas: tuple[str, ...]
    description: str | None
    entities: tuple[Entity, ...] # (entity_value, status)
    source: str                  # EVENTBRIDGE | RECONCILE
```

### 6.2 Event Group 연결 (eventArn 분할·변경 대응)
- `correlationKey = eventTypeCode # eventRegion # startTime(YYYY-MM-DD)`
- 새 eventArn이 들어오면 `correlationKey`로 groupId를 계산한다. 같은 키의 그룹이 이미 있으면 그 그룹에 연결되고, 없으면 새 그룹이 만들어진다. correlationKey에 변경일이 포함되므로 다른 시점의 동일 유형 이벤트와는 섞이지 않는다.
- 같은 계정에 같은 그룹의 Tracking Item이 이미 있으면 새 eventArn을 그 항목의 `eventArns` 집합에 추가한다. 리소스가 많아 여러 eventArn으로 분할된 경우를 흡수하기 위함이다.
- 그룹 연결은 생성 시점에만 판단한다. 이후 `startTime`이 바뀌어도 그룹은 유지된다(eventArn → Tracking Item 매핑으로 추적).

### 6.3 Tracking Item 상태 머신
```
              (Tier 2)                 '조치 필요'
   감지 ─────────────► TRIAGE ───────────────────────► OPEN
     │                   │ '해당 없음'(사유 필수)        │
     │ (Tier 1)          ▼                             │
     └──────────────► OPEN ◄───── 재발(새 PENDING) ──── RESOLVED_AUTO
                         │
       ┌─────────────────┼──────────────────┬─────────────────────┐
       ▼                 ▼                  ▼                     ▼
  RESOLVED_AUTO    RESOLVED_MANUAL     NOT_APPLICABLE        CLOSED_BY_AWS
  (엔티티 전부        ('조치 완료' 버튼)   (사유 필수 /          (이벤트 closed,
   RESOLVED)                           계정 해지·정지)        동적 상태 미지원)
```

| 전이 | 조건 |
|---|---|
| → OPEN | Tier 1 감지, TRIAGE에서 '조치 필요' 판정, D-14 이내에 도달한 TRIAGE 항목(자동 승격) |
| → RESOLVED_AUTO | 동적 상태 지원 이벤트에서 해당 계정 엔티티가 1개 이상 있고 모두 `RESOLVED` |
| → RESOLVED_MANUAL | 담당자가 '조치 완료' 클릭 |
| → NOT_APPLICABLE | 담당자가 '해당 없음' 클릭(사유 필수), 또는 Organizations 계정 상태가 SUSPENDED/CLOSED |
| → CLOSED_BY_AWS | `statusCode = closed` 이고 동적 상태 미지원. 스레드에 "AWS 측 이벤트 종료, 조치 여부를 확인하세요"를 게시하고 리마인더를 중단한다. |
| RESOLVED_AUTO → OPEN | 이후 sweep에서 해당 계정에 `PENDING` 엔티티가 다시 나타남 |

- **동적 상태 지원 판정**: 해당 이벤트의 엔티티 중 하나라도 `PENDING` 또는 `RESOLVED` 상태가 있으면 지원으로 본다. 그렇지 않으면 `manualCompletionRequired = true`로 표시하고 Slack에도 표기한다.
- **수동 완료 후 불일치 검증**: RESOLVED_MANUAL 후 72시간이 지나도 Health API에 `PENDING` 엔티티가 남아 있으면 상태는 유지하되 스레드에 "API상 미조치 리소스 N개 남음"을 1회 게시하고 `verificationMismatch = true`를 기록한다. 이 값은 다이제스트에 노출된다. 72시간은 AWS 상태 반영 최대 지연을 반영한 값이다.
- 기한 경과(Overdue)는 상태가 아니라 파생 속성이다: `state = OPEN` 이고 `dueDate < today`.

### 6.4 `observe(snapshot)` 로직
1. 분류(3장) 결과가 제외이면 종료한다.
2. Event 항목을 upsert한다. 저장된 `lastUpdatedTime`보다 새로운 경우에만 메타데이터를 갱신한다.
3. `(eventArn, accountId)` → Tracking Item을 조회한다. 없으면 그룹을 연결(6.2)하고 생성한다.
4. 엔티티를 upsert한 뒤 집계(`pending`, `resolved`, `total`)를 다시 계산한다.
5. 상태 전이(6.3)와 Due Date 변경(3.4)을 판정한다.
6. 변경 유형별 Intent를 발행한다: `NEW`, `CONTENT_UPDATED`, `DUE_CHANGED`, `PROGRESS`(엔티티 집계 변화), `RESOLVED`, `REOPENED`, `CLOSED_BY_AWS`.
7. 변경이 없으면 Intent를 발행하지 않는다 (재처리 시 무알림 보장).

---

## 7. 저장소 설계

### 7.1 식별자 규칙
- `groupId = sha256(correlationKey)[:16]` (결정적). 새 eventArn은 Event 항목이 없을 때만 correlationKey로 groupId를 계산하고, 이후에는 Event 항목에 저장된 groupId를 따른다.
- `trackingId = {groupId}#{accountId}` (결정적). 별도 ARN 매핑 항목과 correlation GSI가 필요 없다.

### 7.2 `aha-state` (단일 테이블, PK `pk` / SK `sk`, PITR·삭제 보호 활성화)
| 항목 | pk | sk | 주요 속성 |
|---|---|---|---|
| Event | `EVT#{eventArn}` | `META` | groupId, service, eventTypeCode, category, eventRegion, statusCode, startTime, endTime, lastUpdatedTime, actionability, personas, description, descriptionHash, dynamicStatus |
| Event Group | `GRP#{groupId}` | `META` | correlationKey, primaryArn, eventArns(String Set, ADD로 병합) |
| Tracking Item | `GRP#{groupId}` | `TRK#{accountId}` | tier, state, dueDate, dueDateSource, awsDueDate, assignee(Slack user ID), eventArns, remindersSent, manualCompletionRequired, verificationMismatch, counts, createdAt, resolvedAt, manualResolvedAt, version |
| Notice | `GRP#{groupId}` | `NTC#{channelId}` | ts, postedChannelId(대체 채널 게시 시 실제 채널) |
| Entity | `TRK#{trackingId}` | `ENT#{entityValue}` | status, lastSeenAt |
| Audit | `TRK#{trackingId}` | `LOG#{iso8601}#{rand}` | actor(slack user / system), action, before, after, detail |
| Cursor | `CURSOR` | `{name}` | value |

GSI:
| GSI | PK | SK | 용도 |
|---|---|---|---|
| `gsi-state` | `STATE#{state}` | `{dueDate}#{trackingId}` | 리마인더, 다이제스트, entity sweep 대상 조회 |

Route 캐시는 P2(계정 태그 라우팅)에서 추가한다.

### 7.3 `aha-dedup` (P3, EventBridge 수집 시 추가)
- PK `communicationId#accountId#page`, TTL 30일

---

## 8. 라우팅

### 8.1 계정 태그 표준 (Organizations 계정 태그)
| 태그 키 | 값 | 필수 |
|---|---|---|
| `health:slack-channel` | Slack 채널 ID (예: `C0123ABCD`) | 권장 |
| `health:owner-group` | Slack 사용자 그룹 ID (예: `S0123ABCD`), 멘션 대상 | 권장 |
| `health:owner-team` | 팀 이름 (표시용) | 선택 |

### 8.2 결정 규칙
1. 계정 태그 `health:slack-channel`이 있으면 그 채널로 보낸다. 없으면 `default_channel`로 보내고 다이제스트의 '태그 미설정 계정'에 포함한다.
2. 멘션 대상은 `health:owner-group`(D-14 이후 멘션)이다.
3. persona 보조 규칙 (설정 `persona_routes`):
   ```yaml
   persona_routes:
     SECURITY: { mention_group: S0SEC..., channel: C0SEC... }   # channel 지정 시 해당 채널에도 Notice 생성
     BILLING:  { mention_group: S0FIN... }
     OPERATIONS: {}                                              # 계정 소유 팀만
   ```
4. 에스컬레이션 채널은 `escalation_channel` 단일 채널(설정)로 한다.
5. Route는 1시간 동안 캐시한다. 태그를 바꾸면 이후 생성되는 Notice부터 적용되고, 기존 Notice는 옮기지 않는다.

---

## 9. Slack 설계

### 9.1 Slack App
- Bot scopes: `chat:write`, `chat:write.public`(Bot 미초대 공개 채널 게시), `users:read`
- Interactivity Request URL: API Gateway HTTP API `/slack/interactions`
- 시크릿: Secrets Manager `aha/slack` (`bot_token`, `signing_secret`)

### 9.2 Notice 메시지 (채널 내 Event Group당 1개, 상태 변화 시 `chat.update`)
```
🔴 [ACTION REQUIRED · P1] RDS · AWS_RDS_PLANNED_LIFECYCLE_EVENT
Due: 2026-11-30 (D-49)   리전: ap-northeast-2   Persona: OPERATIONS
담당: @홍길동 (미지정 시 "⚠️ 담당자 미지정")
요약: Amazon RDS for MySQL 5.7 Extended Support ... (설명 첫 300자)
──────────────────────────────
• 111122223333 prod-payments    미조치 3 / 5   [⋯ 완료 처리 | 해당 없음]
• 444455556666 prod-orders      ✅ 조치 완료(자동)
• 777788889999 stg-orders       미조치 1 / 1   [⋯]
──────────────────────────────
[담당자 지정] [Due Date 수정] [리소스 보기] [Health Dashboard]
ℹ️ 이 이벤트는 자동 완료 판정을 지원하지 않습니다. 조치 후 '완료 처리'를 눌러 주세요. (manualCompletionRequired일 때만)
```
- TRIAGE(Tier 2) 상태에서는 하단 버튼이 `[조치 필요]` `[해당 없음]`으로 바뀐다.
- 계정 줄이 40개를 넘으면 상위 38개(미조치 우선, Due Date 순)만 표시하고 "외 N개 계정 — [리소스 보기]에서 전체 확인"을 붙인다. Block Kit 50블록 제한 때문이다.
- 계정별 메뉴(overflow): OPEN은 `완료 처리`/`해당 없음`, TRIAGE는 `조치 필요`/`해당 없음`.
- 그룹 내 모든 계정이 종결되면 헤더를 `✅ [완료]`로 바꾸고 버튼을 제거한다.

### 9.3 스레드 게시 (원본 메시지 하위)
| Intent | 스레드 내용 |
|---|---|
| CONTENT_UPDATED | "AWS가 이벤트 내용을 갱신했습니다" + 변경된 설명 |
| DUE_CHANGED | "Due Date 변경: 2026-11-30 → 2027-02-28 (출처: AWS)" |
| PROGRESS | "prod-payments: 미조치 5 → 3" (엔티티 sweep당 1회로 묶음) |
| RESOLVED / REOPENED / CLOSED_BY_AWS | 상태 변경과 사유 |
| 리마인더 | "D-14 · 미조치 계정 2개" + 멘션 |
| 액션 기록 | "@홍길동 님이 prod-orders를 '해당 없음' 처리: 사유 ..." |

### 9.4 인터랙션
| 액션 | UI | 처리 |
|---|---|---|
| 담당자 지정 | users_select | 그룹 내 OPEN 항목 전체의 `assignee` 설정 |
| Due Date 수정 | 모달(datepicker + 사유) | `dueDate` 변경, `dueDateSource = MANUAL`, `remindersSent` 재계산 |
| 리소스 보기 | 모달 | 계정별 엔티티와 상태 목록 (최대 100개, 초과 시 개수만 표시) |
| 완료 처리 (계정 단위) | overflow → 확인 모달 | RESOLVED_MANUAL |
| 해당 없음 | 모달(사유 필수) | NOT_APPLICABLE |
| 조치 필요 (TRIAGE) | 버튼 | TRIAGE → OPEN, Due Date 산정 |

- Slack의 3초 응답 제한 때문에, Interaction Lambda는 서명 검증과 DynamoDB 조건부 쓰기까지만 동기로 처리하고 메시지 갱신은 notify 큐로 넘긴다. 모달은 `trigger_id`로 즉시 `views.open`한다.
- 모든 액션은 Audit 항목에 actor와 사유를 남긴다.

### 9.5 리마인더 / 에스컬레이션 (매일 09:00 KST)
대상: `state = OPEN`
| 남은 일수 | 동작 |
|---|---|
| D-90, 60, 30 | 스레드 리마인더 (멘션 없음) |
| D-14, 7, 3, 1, D-day | 스레드 리마인더 + `owner-group`/담당자 멘션 |
| D-7 | 추가: `escalation_channel`에 해당 항목 게시 |
| D+1 이후 (Overdue) | 매일 스레드 리마인더 + 멘션. `escalation_channel`에는 개별 게시 대신 **일일 Overdue 목록 1건** |

- 이미 발송한 마일스톤은 `remindersSent`에 기록해 다시 보내지 않는다. 늦게 감지된 항목은 지난 마일스톤을 건너뛰고 다음 마일스톤부터 보낸다.
- 같은 그룹, 같은 채널의 여러 계정 리마인더는 Notice 스레드 1건으로 묶는다.
- TRIAGE 항목: 생성 후 7일까지 판정이 없으면 스레드 리마인더를 1회 보낸다. Due Date가 D-14 이내로 들어오면 자동으로 OPEN 승격하고 스레드에 알린다.
- 담당자 미지정 OPEN 항목은 D-14부터 리마인더에 "⚠️ 담당자 미지정"을 강조한다.

### 9.6 주간 다이제스트 (월 09:00 KST)
- **팀 채널별**: Overdue, 30일 이내 마감, TRIAGE 대기, 담당자 미지정, `verificationMismatch` 항목 목록 (Due Date 순)
- **전사(escalation 채널)**: 팀별 OPEN/Overdue 건수, 태그 미설정 계정, 시스템 상태(마지막 reconcile 성공 시각, DLQ 적재 건수)

### 9.7 Slack 전송 안정성
- notify 큐는 **SQS FIFO**이며 `MessageGroupId = groupId`이다. 한 Event Group의 모든 Notice 게시·수정이 순서대로 처리되어 최초 게시 경합으로 메시지가 2개 생기지 않는다.
- Notice 최초 생성: `chat.postMessage` → `ts` 저장. Notifier가 그룹 단위로 직렬 처리되므로 별도 잠금이 필요 없다.
- Intent 유실 대비: entity sweep마다 비종결 항목이 있는데 Notice가 없는 채널을 찾아 `REFRESH` Intent를 다시 발행한다.
- `429`, `5xx`, 타임아웃: 해당 메시지를 실패 처리해 SQS visibility timeout 후 재시도(최대 5회)하고, 이후 DLQ로 보낸다.
- `channel_not_found`, `not_in_channel`, `is_archived`: `default_channel`로 대체 게시하고, 다이제스트에 '라우팅 오류 계정'으로 노출한다.

---

## 10. 오류 처리 및 운영 감시

| 상황 | 처리 |
|---|---|
| 스키마 검증 실패 | ingest DLQ |
| 낙관적 잠금 충돌 | 최대 3회 재조회 후 재적용, 이후 SQS 재시도 |
| Health API 스로틀링 / 장애 | adaptive retry. 실패한 실행은 커서를 전진시키지 않아 다음 실행에서 보정 |
| Organizations 조회 실패 | 캐시된 Route 사용. 캐시가 없으면 `default_channel` |
| Slack 장애 | notify 큐에 적재된 상태로 재시도. DLQ 메시지는 재처리 스크립트(`redrive`)로 다시 넣음 |

CloudWatch 알람 (SNS → 운영 이메일, Slack과 별도 경로):
- 각 DLQ `ApproximateNumberOfMessagesVisible > 0`
- Reconcile 마지막 성공 후 3시간 경과 (heartbeat 메트릭 부재)
- Lambda Errors > 0 (5분)
- `ReconcileMissedItems > 0` (full resync에서 누락이 발견됨 → 실시간 경로 점검 필요)
- `UnnotifiedTier1Items > 0`: 생성 후 15분이 지나도 Notice가 `POSTED`가 아닌 Tier 1 항목

로그: Powertools 구조화 로그(JSON). `trackingId`, `eventArn`, `accountId`를 상관관계 키로 쓴다. 토큰과 시크릿은 로그에 남기지 않는다.

---

## 11. 보안

- **IAM 최소 권한**
  - Reconcile: `health:DescribeEventsForOrganization`, `DescribeEventDetailsForOrganization`, `DescribeAffectedAccountsForOrganization`, `DescribeAffectedEntitiesForOrganization`
  - Routing: delegation policy로 위임된 Organizations 읽기 권한만
  - 각 Lambda는 필요한 DynamoDB 테이블, SQS 큐, 시크릿에만 접근
  - 시스템에 쓰기 권한이 있는 AWS 리소스는 자체 테이블과 큐뿐이다. Health와 Organizations는 읽기 전용이다.
- **Slack 엔드포인트**: Slack 특성상 공개 엔드포인트가 필요하다. 요청마다 `X-Slack-Signature`(v0 HMAC-SHA256)와 `X-Slack-Request-Timestamp`(±5분)를 검증하고, 실패하면 401로 거부한다(fail-closed). API Gateway throttling을 설정하고, 필요하면 WAF rate-based rule을 붙인다.
- **암호화**: DynamoDB, SQS, Secrets Manager 모두 KMS 암호화. DynamoDB PITR 활성화.
- **감사**: 모든 수동 상태 변경은 Slack user ID와 사유를 Audit에 영구 보존한다.

---

## 12. Terraform / 코드 구조

```
infra/
  modules/
    regional-forwarder/   # 리전별 EventBridge rule + cross-region target role
    core/                 # bus, SQS, DynamoDB, Lambda, Scheduler, API GW, alarms
  envs/
    dev/  main.tf         # provider alias로 수집 리전마다 regional-forwarder 인스턴스화
    prod/ main.tf
src/aha/
  domain/      # classify.py, due_date.py, state_machine.py, grouping.py, observe.py, actions.py
  adapters/    # health_client.py, org_client.py, repo_dynamodb.py, slack_client.py
  slack/       # blocks.py (렌더링), signature.py, interactions.py
  handlers/    # ingest.py, reconcile.py, reminder.py, digest.py, notifier.py, interaction.py
  config.py    # SSM Parameter Store에서 설정 로드 (SLA, persona_routes, 채널, 리스트)
tests/
  fixtures/    # 실제 형식의 EventBridge/Health API 샘플 페이로드
  unit/ integration/
```
- `domain/`은 AWS SDK와 Slack에 의존하지 않는 순수 로직이다. 어댑터는 인터페이스(Protocol)로 주입한다.
- 설정값은 SSM Parameter Store(`/aha/{env}/config`, YAML)에 둔다. 설정만 바꿀 때는 재배포하지 않는다.

---

## 13. 테스트 전략

| 레벨 | 대상 | 방법 |
|---|---|---|
| Unit | 분류, Fallback, Due Date, 우선순위, 마일스톤 계산, 상태 전이, 그룹 연결 | pytest, 고정 시각(freezegun), 카테고리별 fixture |
| Unit | Block Kit 렌더링 (45계정 초과 절단 포함) | 스냅샷 테스트 |
| Unit | Slack 서명 검증 (정상, 변조, 만료) | 고정 벡터 |
| Integration | Ingest/Reconcile → DynamoDB → notify 큐, 멱등성(같은 메시지 3회 투입 시 Intent 1회), 낙관적 잠금 충돌 | moto (DynamoDB, SQS) + Fake Health/Slack 어댑터 |
| Integration | eventArn 분할 시나리오, Due Date 연장, RESOLVED 후 재발, 수동 완료 후 불일치 | 시나리오 fixture |
| E2E (dev) | 합성 이벤트(`custom.aha.test`) → 테스트 Slack 채널 | 수동 확인 + 자동 검증 스크립트 |
| Shadow (prod) | 실제 Health 데이터를 `DRY_RUN` 모드로 처리해 테스트 채널에만 게시 | 2주간 메일 수신 내역과 대조 |

---

## 14. 단계별 도입 계획

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| P0 준비 | Slack App 생성, 계정 태그 표준 적용, Organizations delegation policy, 설정값 확정(15장) | 전체 계정의 80% 이상 태그 적용 |
| P1 MVP | Reconcile(event/entity sweep, full resync) + Domain Core + Notice + 수동 액션. Shadow 채널에서 운영 | 2주간 메일 대비 누락 0건, 중복 메시지 0건 |
| P2 운영 오픈 | 팀 채널 라우팅, 리마인더, 에스컬레이션, 주간 다이제스트 | KPI G3, G6 측정 시작 |
| P3 실시간화 | 리전별 EventBridge forwarder + Ingest | KPI G5 충족, `ReconcileMissedItems` 0 유지 |

---

## 15. 확인 필요 사항 (기본값 포함)

| # | 항목 | 기본값 / 대응 |
|---|---|---|
| Q1 | EventBridge와 Health Org API 응답에 `actionability`, `personas`가 실제로 포함되는지, `DescribeEventsForOrganization` 필터가 actionability를 지원하는지 | P1 첫 주에 실제 페이로드로 검증. 필터를 지원하지 않으면 클라이언트 측에서 필터링하고, 필드가 없으면 3.2 Fallback 적용 |
| Q2 | Health Dashboard 조직 이벤트 상세 딥링크 형식 | P1에서 확인. 불가 시 Health Dashboard 조직 뷰 메인 링크 |
| Q3 | 운영(수집) 리전 목록 | 계정 전체의 사용 리전 조회 결과로 확정 |
| Q4 | 기본 SLA (issue 3일 / SECURITY 7일 / 기타 30일) | 승인 필요 |
| Q5 | `default_channel`, `escalation_channel`, persona별 Slack 그룹 ID | P0에서 확정 |
| Q6 | 계정 태그 키 (`health:slack-channel`, `health:owner-group`, `health:owner-team`) | 사내 태그 정책과 충돌 여부 확인 |
| Q7 | 홈 리전 | `ap-northeast-2` |
| Q8 | `force_track_event_types` / `suppress_event_types` 초기 목록 | 빈 목록으로 시작, Shadow 운영 결과로 보정 |
