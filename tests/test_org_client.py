import boto3
import pytest
from botocore.stub import Stubber

from aha.adapters.org_client import AccountInfo, OrgDirectory, owner_alias


def _account(account_id, name, email, status="ACTIVE"):
    return {
        "Id": account_id,
        "Arn": f"arn:aws:organizations::999999999999:account/o-abc/{account_id}",
        "Email": email,
        "Name": name,
        "Status": status,
    }


def test_accounts_paginates_and_caches():
    client = boto3.client("organizations", region_name="us-east-1")
    stub = Stubber(client)
    stub.add_response("list_accounts", {"Accounts": [_account("111122223333", "dev", "Usheep+Sub02@amazon.com")], "NextToken": "t1"}, {})
    stub.add_response("list_accounts", {"Accounts": [_account("444455556666", "prod", "ops@amazon.com", "SUSPENDED")]}, {"NextToken": "t1"})
    with stub:
        directory = OrgDirectory(client)
        accounts = directory.accounts()
        assert directory.accounts() is accounts  # 두 번째 호출은 API를 다시 부르지 않는다
    stub.assert_no_pending_responses()
    assert accounts == {
        "111122223333": AccountInfo("111122223333", "dev", "Usheep+Sub02@amazon.com", "ACTIVE"),
        "444455556666": AccountInfo("444455556666", "prod", "ops@amazon.com", "SUSPENDED"),
    }


@pytest.mark.parametrize(
    ("email", "alias"),
    [
        ("Usheep+Sub02@amazon.com", "usheep"),
        ("usheep@amazon.com", "usheep"),
        ("OPS-Team@example.com", "ops-team"),
        (" usheep@amazon.com ", "usheep"),
        ("usheep", None),
        ("", None),
        ("@amazon.com", None),
        ("+sub@amazon.com", None),
    ],
)
def test_owner_alias(email, alias):
    assert owner_alias(email) == alias
