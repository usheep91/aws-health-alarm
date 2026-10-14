from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit

from aha.handlers import deps
from aha.reminder import ReminderService

logger = Logger()
metrics = Metrics()


@metrics.log_metrics
@logger.inject_lambda_context
def lambda_handler(event, context):
    service = ReminderService(deps.repository(), deps.publisher(), deps.reminder_slack(), deps.org_directory(), deps.config())
    result = service.run(datetime.now(UTC), time_left=lambda: context.get_remaining_time_in_millis() / 1000)
    # 알람은 service 차원만으로 조회하므로 추가 차원을 붙이지 않는다.
    metrics.add_metric(name="ReminderSuccess", unit=MetricUnit.Count, value=1)
    # dataclass 필드를 최상위 extra로 펼치면 LogRecord 예약 속성과 충돌할 수 있어 중첩 키로 남긴다.
    logger.info("reminder finished", extra={"result": asdict(result)})
    return asdict(result)
