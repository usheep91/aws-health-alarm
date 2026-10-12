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
