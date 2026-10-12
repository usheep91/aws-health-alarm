from __future__ import annotations

import uuid
from collections.abc import Iterable

from aha.domain.models import Intent

SQS_BATCH_LIMIT = 10


class SqsIntentPublisher:
    def __init__(self, client, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def publish(self, intents: Iterable[Intent]) -> None:
        pending = list(intents)
        for start in range(0, len(pending), SQS_BATCH_LIMIT):
            entries = []
            for n, intent in enumerate(pending[start : start + SQS_BATCH_LIMIT]):
                body = intent.to_json()
                entries.append(
                    {
                        "Id": str(n),
                        "MessageBody": body,
                        # 그룹 단위 직렬 처리로 Notice 중복 게시를 막는다.
                        "MessageGroupId": intent.group_id,
                        # 동일 내용의 정당한 반복 intent가 5분 dedup 창에서 버려지지 않도록 매번 고유 ID.
                        "MessageDeduplicationId": uuid.uuid4().hex,
                    }
                )
            resp = self._client.send_message_batch(QueueUrl=self._queue_url, Entries=entries)
            if resp.get("Failed"):
                raise RuntimeError(f"intent publish failed: {resp['Failed']}")
