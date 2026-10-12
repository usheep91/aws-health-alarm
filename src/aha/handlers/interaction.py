from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs

from aws_lambda_powertools import Logger

from aha.domain.actions import InvalidAction
from aha.domain.repository import ConcurrentModification
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
    except (InvalidAction, ConcurrentModification) as e:
        logger.info("action not applied", extra={"reason": str(e), "error_type": type(e).__name__})
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
