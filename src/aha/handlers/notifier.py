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
# 실패한 메시지 그룹만 재시도로 돌리고, 다른 그룹(다른 이벤트·내보내기)은 계속 처리한다.
processor = SqsFifoPartialProcessor(skip_group_on_error=True)


def record_handler(record: SQSRecord) -> None:
    intent = Intent.from_json(record.body)
    cfg = deps.config()
    logger.append_keys(group_id=intent.group_id, tracking_id=intent.tracking_id, kind=intent.kind.value)
    Notifier(deps.repository(), deps.slack(), deps.router(), cfg).handle(intent, to_local_date(datetime.now(UTC), cfg.timezone))


@logger.inject_lambda_context
def lambda_handler(event, context):
    # 실패한 메시지와 같은 그룹의 후속 메시지는 재시도로 돌려 순서를 보장한다.
    return process_partial_response(event=event, record_handler=record_handler, processor=processor, context=context)
