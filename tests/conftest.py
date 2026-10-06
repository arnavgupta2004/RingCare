import os

import boto3
import pytest

# Fake credentials for moto: tests must never reach real AWS.
os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1",
})


@pytest.fixture
def aws():
    from moto import mock_aws

    with mock_aws():
        yield


@pytest.fixture(params=["sqlite", "dynamodb"])
def store(request):
    """Every StateStore test runs against both backends."""
    from backend.store import SQLiteStore

    if request.param == "sqlite":
        yield SQLiteStore(":memory:")
        return
    from moto import mock_aws

    from backend.store.dynamodb import DynamoDBStore, create_table

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        create_table(resource, "doorsight-test")
        yield DynamoDBStore("doorsight-test", resource=resource)
