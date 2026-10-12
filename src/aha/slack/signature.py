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
