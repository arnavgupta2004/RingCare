"""Delivery of caregiver notifications: SNS email when SNS_TOPIC_ARN is set, else a log file.

Resident notifications are shown in the resident web view and are not sent anywhere.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend.config import get_settings
from backend.store import Notification

logger = logging.getLogger("notify")

SUBJECTS = {
    "package_missing": "DoorSight: possible missing package",
    "unusual_hour": "DoorSight: unusual-hour activity at the front door",
}


def _subject(n: Notification) -> str:
    return SUBJECTS.get(n.kind, "DoorSight alert")[:100]  # SNS email subject limit


def _body(n: Notification) -> str:
    return (
        f"{n.text}\n\n"
        f"Time (home): {n.sim_ts}\n"
        f"Event: {n.event_id or '-'}\n"
        f"Based on: {n.source}{' (automatic estimate from the local detector)' if n.source == 'stub' else ''}\n"
    )


class LogNotifier:
    """Fallback: append caregiver alerts to logs/notifications.log."""

    name = "log"

    def __init__(self, path=None):
        self.path = path

    def send(self, n: Notification) -> str:
        line = json.dumps({"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "subject": _subject(n), **n.to_dict()})
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
        logger.info("caregiver alert (not sent, SNS not configured): %s", n.text)
        return "logged"


class SNSNotifier:
    name = "sns"

    def __init__(self, topic_arn: str, client=None, fallback: LogNotifier | None = None):
        self.topic_arn = topic_arn
        self.fallback = fallback or LogNotifier()
        self.client = client or boto3.client(
            "sns", region_name=topic_arn.split(":")[3],
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3}),
        )

    def send(self, n: Notification) -> str:
        try:
            resp = self.client.publish(TopicArn=self.topic_arn, Subject=_subject(n), Message=_body(n))
            n.extra["sns_message_id"] = resp["MessageId"]
            logger.info("caregiver alert sent via SNS (%s): %s", resp["MessageId"], n.text)
            return "sent"
        except (BotoCoreError, ClientError) as exc:
            logger.error("SNS publish failed, logging instead: %s", exc)
            n.extra["send_error"] = str(exc)
            self.fallback.send(n)
            return "failed"


def notifier_from_env() -> LogNotifier | SNSNotifier:
    settings = get_settings()  # loads .env
    log = LogNotifier(settings.logs_dir / "notifications.log")
    topic = os.getenv("SNS_TOPIC_ARN", "").strip()
    return SNSNotifier(topic, fallback=log) if topic else log
