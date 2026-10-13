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
    result = reconciler.run(mode, datetime.now(UTC), time_left=lambda: context.get_remaining_time_in_millis() / 1000)
    metrics.add_dimension(name="mode", value=mode)
    if result.completed:
        metrics.add_metric(name="ReconcileSuccess", unit=MetricUnit.Count, value=1)
        if mode == "full":
            metrics.add_metric(name="ReconcileMissedItems", unit=MetricUnit.Count, value=result.created)
    else:
        # 시간 예산 소진으로 중단됨. 커서를 전진시키지 않았으므로 다음 실행이 이어서 처리한다.
        metrics.add_metric(name="ReconcileIncomplete", unit=MetricUnit.Count, value=1)
    # 'created'는 LogRecord 예약 속성이라 최상위 extra 키로 펼치면 KeyError가 난다.
    logger.info("reconcile finished", extra={"mode": mode, "result": asdict(result)})
    return asdict(result)
