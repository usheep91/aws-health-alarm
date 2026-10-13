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

배포 시 참고:

- 최초 배포 후 몇 시간 동안은 reconcile 실행 이력이 쌓이지 않아 heartbeat 알람이 한 번 울릴 수 있다. 첫 sweep이 성공하면 해소된다.
- Slack 시크릿을 고객 관리형 KMS 키(CMK)로 암호화했다면 notifier/interaction Lambda 역할에 해당 키의 `kms:Decrypt` 권한을 추가해야 한다.
- dev DynamoDB 테이블은 삭제 보호(`deletion_protection_enabled`)가 켜져 있다. `terraform destroy` 전에 명시적으로 해제해야 한다.
- CSV 내보내기를 쓰려면 Slack App에 `files:write`, `im:write` 스코프가 필요하다. 스코프를 추가한 뒤 워크스페이스에 앱을 다시 설치한다. Bot token이 바뀌면 Secrets Manager 시크릿을 `put-secret-value`로 갱신하고, 이미 떠 있는(warm) Lambda는 콜드 스타트 전까지 이전 토큰을 유지하므로 재배포하거나 함수 설정을 업데이트해 갱신한다.
- 프로바이더 버전은 `infra/envs/dev/.terraform.lock.hcl`로 고정되어 있다. 업그레이드는 `init -upgrade` 후 lock 파일 변경을 검토해 커밋한다.

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
| `*-reconcile-incomplete-{full,entity}` | full/entity sweep이 Lambda 시간 예산(종료 60초 전) 소진으로 중단됨 | reconcile 로그에서 처리량·Health API 지연 확인. 이벤트/항목 수가 많으면 sweep 분할 또는 주기 조정 검토 |
| `*-notify-dlq-not-empty` | Slack 게시 5회 실패 | DLQ 메시지 확인 후 원인 해결, SQS 콘솔의 DLQ redrive로 재처리 |
| `*-<fn>-errors` | Lambda 오류 | 해당 함수 로그 확인 |
