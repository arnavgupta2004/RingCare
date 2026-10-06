"""S3 snapshots, SNS caregiver alerts and env-based backend selection (moto-mocked AWS)."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.config import Config

from backend.clock import DemoClock
from backend.doorstep import Doorstep, VisitProfile
from backend.notify import LogNotifier, SNSNotifier, notifier_from_env
from backend.snapshots import LocalSnapshots, S3Snapshots, snapshots_from_env
from backend.store import Notification, SQLiteStore, store_from_env

REAL = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)


def _frames(tmp_path, names=("frame_000.jpg", "frame_005.jpg", "frame_009.jpg")):
    d = tmp_path / "frames" / "cap1"
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"\xff\xd8 jpeg " + n.encode())
    return [f"frames/cap1/{n}" for n in names]


def _note(audience="caregiver", kind="unusual_hour"):
    return Notification(id="n1", audience=audience, kind=kind, text="Unusual-hour activity at the front door.",
                        event_id="e1", source="stub", sim_ts="2026-10-07T03:00:00+05:30",
                        real_ts="2026-10-06T08:00:00+05:30")


# --- S3 snapshots ---------------------------------------------------------------

def _s3():
    return boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))  # as in production


def test_s3_publish_uploads_all_frames_and_presigns(aws, tmp_path):
    s3 = _s3()
    s3.create_bucket(Bucket="snaps")
    rels = _frames(tmp_path)
    store = S3Snapshots("snaps", client=s3, data_dir=tmp_path)

    ref = store.publish(rels)
    assert ref == "s3://snaps/frames/cap1/frame_000.jpg"
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket="snaps")["Contents"])
    assert keys == sorted(rels)
    head = s3.head_object(Bucket="snaps", Key=rels[0])
    assert head["ContentType"] == "image/jpeg" and head["ServerSideEncryption"] == "AES256"

    url = urlparse(store.url(ref))
    assert url.path.endswith("/frames/cap1/frame_000.jpg") and "snaps" in (url.netloc + url.path)
    q = parse_qs(url.query)
    assert "X-Amz-Signature" in q and q["X-Amz-Expires"] == ["3600"]


def test_s3_upload_failure_falls_back_to_local_reference(aws, tmp_path):
    s3 = _s3()  # bucket never created
    rels = _frames(tmp_path)
    store = S3Snapshots("missing-bucket", client=s3, data_dir=tmp_path)
    assert store.publish(rels) == rels[0]
    assert store.url(rels[0]) == f"/media/{rels[0]}"


def test_local_snapshots():
    local = LocalSnapshots()
    assert local.publish(["frames/a/f.jpg", "frames/a/g.jpg"]) == "frames/a/f.jpg"
    assert local.url("frames/a/f.jpg") == "/media/frames/a/f.jpg"
    assert local.publish([]) is None and local.url(None) is None


# --- SNS ----------------------------------------------------------------------

def _topic_with_queue():
    """SNS topic with an SQS subscriber so the test can read what was published."""
    sns = boto3.client("sns", region_name="us-east-1")
    sqs = boto3.client("sqs", region_name="us-east-1")
    arn = sns.create_topic(Name="alerts")["TopicArn"]
    q = sqs.create_queue(QueueName="inbox")["QueueUrl"]
    q_arn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=arn, Protocol="sqs", Endpoint=q_arn)
    return sns, sqs, arn, q


def test_sns_notifier_publishes_subject_and_body(aws):
    sns, sqs, arn, q = _topic_with_queue()
    n = _note()
    assert SNSNotifier(arn, client=sns).send(n) == "sent"
    assert n.extra["sns_message_id"]
    [msg] = sqs.receive_message(QueueUrl=q)["Messages"]
    body = json.loads(msg["Body"])
    assert body["Subject"] == "DoorSight: unusual-hour activity at the front door"
    assert "Unusual-hour activity at the front door." in body["Message"]
    assert "automatic estimate" in body["Message"]


def test_sns_failure_falls_back_to_log(aws, tmp_path):
    sns = boto3.client("sns", region_name="us-east-1")
    log = tmp_path / "notifications.log"
    n = _note()
    status = SNSNotifier("arn:aws:sns:us-east-1:123456789012:nope", client=sns, fallback=LogNotifier(log)).send(n)
    assert status == "failed" and "send_error" in n.extra
    assert json.loads(log.read_text())["subject"].startswith("DoorSight")


def test_doorstep_sends_only_caregiver_notifications(aws):
    sns, sqs, arn, q = _topic_with_queue()
    clock = DemoClock(tz="Asia/Kolkata", real_now=lambda: REAL)
    ds = Doorstep(SQLiteStore(":memory:"), clock, reminder_hours=3, profile=VisitProfile(),
                  notifier=SNSNotifier(arn, client=sns))
    summary = {"vehicle": {"frame_fraction": 1.0}, "package": {"frame_fraction": 1.0}}
    clock.set(datetime(2026, 10, 6, 14, 10))
    ds.record_event("e-pkg", "package", analysis={"frame_count": 20, "detections": {"summary": summary}})
    clock.set(datetime(2026, 10, 7, 3, 0))
    ds.record_event("e-night", "vehicle", analysis={"frame_count": 20, "detections": {"summary": summary}})

    statuses = {(n.audience, n.kind): n.status for n in ds.store.list_notifications()}
    assert statuses[("resident", "package_arrived")] == "queued"
    assert statuses[("caregiver", "unusual_hour")] == "sent"
    assert len(sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=10)["Messages"]) == 1


# --- env selection --------------------------------------------------------------

def test_backend_selection_from_env(aws, monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "sqlite")
    monkeypatch.setenv("SNAPSHOT_BACKEND", "local")
    monkeypatch.delenv("SNS_TOPIC_ARN", raising=False)
    assert type(store_from_env()).__name__ == "SQLiteStore"
    assert snapshots_from_env().name == "local"
    assert notifier_from_env().name == "log"

    monkeypatch.setenv("STATE_BACKEND", "dynamodb")
    monkeypatch.setenv("SNAPSHOT_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "snaps")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:alerts")
    assert type(store_from_env()).__name__ == "DynamoDBStore"
    assert snapshots_from_env().name == "s3"
    assert notifier_from_env().name == "sns"


@pytest.mark.parametrize("var,value", [("STATE_BACKEND", "postgres"), ("SNAPSHOT_BACKEND", "ftp")])
def test_backend_selection_rejects_unknown_values(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError):
        (store_from_env if var == "STATE_BACKEND" else snapshots_from_env)()


def test_s3_backend_requires_bucket(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "")
    with pytest.raises(ValueError, match="S3_BUCKET"):
        snapshots_from_env()
