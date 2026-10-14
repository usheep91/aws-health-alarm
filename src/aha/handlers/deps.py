"""Lambda 실행 환경용 의존성. 모두 지연 생성되어 import 시점에는 AWS를 호출하지 않는다.

설정은 콜드 스타트마다 SSM에서 다시 읽는다.
"""
from __future__ import annotations

import json
import os
from functools import cache

import boto3
from slack_sdk import WebClient

from aha.adapters.health_client import HealthClient
from aha.adapters.intent_queue import SqsIntentPublisher
from aha.adapters.org_client import OrgDirectory
from aha.adapters.repo_dynamodb import DynamoRepository
from aha.adapters.slack_client import SlackGateway
from aha.config import Config, load_config
from aha.routing import StaticRouter

SLACK_TIMEOUT_SECONDS = 5


@cache
def config() -> Config:
    return load_config(boto3.client("ssm"), os.environ["CONFIG_PARAM"])


@cache
def repository() -> DynamoRepository:
    return DynamoRepository(boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]))


@cache
def publisher() -> SqsIntentPublisher:
    return SqsIntentPublisher(boto3.client("sqs"), os.environ["NOTIFY_QUEUE_URL"])


@cache
def health() -> HealthClient:
    return HealthClient()


@cache
def org_directory() -> OrgDirectory:
    return OrgDirectory()


@cache
def slack_secret() -> dict:
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SLACK_SECRET_ARN"])
    return json.loads(secret["SecretString"])


@cache
def slack() -> SlackGateway:
    return SlackGateway(WebClient(token=slack_secret()["bot_token"], timeout=SLACK_TIMEOUT_SECONDS))


@cache
def router() -> StaticRouter:
    cfg = config()
    return StaticRouter(cfg.shadow_channel or cfg.default_channel)
