from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.config import Config as BotoConfig


@dataclass(frozen=True)
class AccountInfo:
    account_id: str
    name: str
    email: str
    status: str


class OrgDirectory:
    """AWS Organizations 계정 목록 읽기 전용 래퍼. 인스턴스당 한 번만 조회한다."""

    def __init__(self, client=None) -> None:
        self._client = client or self.default_client()
        self._accounts: dict[str, AccountInfo] | None = None

    @staticmethod
    def default_client():
        return boto3.client("organizations", config=BotoConfig(retries={"mode": "adaptive", "max_attempts": 10}))

    def accounts(self) -> dict[str, AccountInfo]:
        if self._accounts is None:
            accounts: dict[str, AccountInfo] = {}
            for page in self._client.get_paginator("list_accounts").paginate():
                for a in page.get("Accounts", []):
                    accounts[a["Id"]] = AccountInfo(a["Id"], a.get("Name", ""), a.get("Email", ""), a.get("Status", ""))
            self._accounts = accounts
        return self._accounts


def owner_alias(email: str) -> str | None:
    """계정 이메일의 로컬 파트에서 '+접미사'를 떼고 소문자로 만든 alias. 형식이 아니면 None."""
    local, at, domain = (email or "").strip().partition("@")
    if not at or not domain:
        return None
    alias = local.split("+", 1)[0].lower()
    return alias or None
