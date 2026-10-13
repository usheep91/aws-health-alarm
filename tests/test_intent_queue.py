import boto3
from moto import mock_aws

from aha.adapters.intent_queue import SqsIntentPublisher
from aha.domain.models import Intent, IntentKind


@mock_aws
def test_publish_batches_with_group_ids():
    sqs = boto3.client("sqs", region_name="ap-northeast-2")
    url = sqs.create_queue(QueueName="t.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    intents = [Intent(IntentKind.REFRESH, f"g{n}", data={"n": n}) for n in range(12)]
    SqsIntentPublisher(sqs, url).publish(intents)

    received = []
    while True:
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, AttributeNames=["MessageGroupId"]).get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            received.append(m)
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
    assert sorted(Intent.from_json(m["Body"]).data["n"] for m in received) == list(range(12))
    assert {m["Attributes"]["MessageGroupId"] for m in received} == {f"g{n}" for n in range(12)}


def test_publish_nothing_makes_no_call():
    class Boom:
        def send_message_batch(self, **kwargs):
            raise AssertionError("should not be called")

    SqsIntentPublisher(Boom(), "url").publish([])


@mock_aws
def test_identical_intents_published_separately_are_both_received():
    sqs = boto3.client("sqs", region_name="ap-northeast-2")
    url = sqs.create_queue(QueueName="t.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    publisher = SqsIntentPublisher(sqs, url)
    intent = Intent(IntentKind.ACTION, "g1", None, {"action": "ASSIGN", "actor": "U1", "accounts": ["1"], "assignee": "U2"})
    publisher.publish([intent])
    publisher.publish([intent])

    bodies = []
    while True:
        msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            bodies.append(m["Body"])
            sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
    assert len(bodies) == 2


@mock_aws
def test_export_intents_use_separate_message_group():
    sqs = boto3.client("sqs", region_name="ap-northeast-2")
    url = sqs.create_queue(QueueName="t.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    intents = [Intent(IntentKind.EXPORT, "g1", None, {"channel": "C1", "requester": "U1"}), Intent(IntentKind.REFRESH, "g1")]
    SqsIntentPublisher(sqs, url).publish(intents)
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, AttributeNames=["MessageGroupId"])["Messages"]
    by_kind = {Intent.from_json(m["Body"]).kind: m for m in msgs}
    assert by_kind[IntentKind.EXPORT]["Attributes"]["MessageGroupId"] == "g1#export"
    assert Intent.from_json(by_kind[IntentKind.EXPORT]["Body"]).group_id == "g1"
    assert by_kind[IntentKind.REFRESH]["Attributes"]["MessageGroupId"] == "g1"
