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
