from __future__ import annotations

from typing import Protocol

from aha.domain.models import TrackingItem


class Router(Protocol):
    def channels_for(self, account_id: str, personas: tuple[str, ...]) -> list[str]: ...
    def account_name(self, account_id: str) -> str | None: ...


class StaticRouter:
    """P1 섀도 운영: 모든 계정을 단일 채널로 보낸다. P2에서 계정 태그 기반 Router로 교체한다."""

    def __init__(self, channel: str) -> None:
        self._channel = channel

    def channels_for(self, account_id: str, personas: tuple[str, ...]) -> list[str]:
        return [self._channel]

    def account_name(self, account_id: str) -> str | None:
        return None


def items_for_channel(
    items: list[TrackingItem], channel: str, router: Router, personas: tuple[str, ...]
) -> list[TrackingItem]:
    return [i for i in items if channel in router.channels_for(i.account_id, personas)]
