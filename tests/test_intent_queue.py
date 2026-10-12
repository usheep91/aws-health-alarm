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
