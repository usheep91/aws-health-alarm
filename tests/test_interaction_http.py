import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

from aha.domain.actions import InvalidAction
from aha.domain.repository import ConcurrentModification
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


def test_concurrent_modification_is_acknowledged():
    service = RecordingService(exc=ConcurrentModification("t"))
    assert handle_http(_event({"type": "block_actions"}), SECRET, service, NOW)["statusCode"] == 200
