"""Where event snapshots live: local disk (served at /media/frames) or a private S3 bucket.

SNAPSHOT_BACKEND=local (default) | s3 (bucket S3_BUCKET). Events store a reference:
    local: "frames/<capture>/frame_010.jpg"           -> URL "/media/frames/<capture>/frame_010.jpg"
    s3:    "s3://<bucket>/frames/<capture>/frame_010.jpg" -> short-lived presigned GET URL
The bucket stays private; the UI only ever sees presigned URLs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend.config import PROJECT_ROOT, get_settings

logger = logging.getLogger("snapshots")

PRESIGN_SECONDS = 3600


class LocalSnapshots:
    name = "local"

    def publish(self, rel_paths: list[str]) -> str | None:
        """Return the reference for the first (representative) frame; nothing to upload."""
        return rel_paths[0] if rel_paths else None

    def url(self, ref: str | None) -> str | None:
        return f"/media/{ref}" if ref and ref.startswith("frames/") else None


class S3Snapshots:
    name = "s3"

    def __init__(self, bucket: str, client=None, data_dir: Path | None = None):
        self.bucket = bucket
        self.data_dir = data_dir or PROJECT_ROOT / "data"
        self.client = client or boto3.client(
            "s3", region_name=os.getenv("AWS_REGION", "us-east-1"),
            config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 3},
                          signature_version="s3v4"),
        )

    def publish(self, rel_paths: list[str]) -> str | None:
        """Upload the frames (first = representative) and return an s3:// reference to the first.

        On upload failure the local reference is returned so the UI still has a picture.
        """
        if not rel_paths:
            return None
        try:
            for rel in rel_paths:
                self.client.upload_file(
                    str(self.data_dir / rel), self.bucket, rel,
                    ExtraArgs={"ContentType": "image/jpeg", "ServerSideEncryption": "AES256"},
                )
            logger.info("uploaded %d snapshot(s) to s3://%s/%s", len(rel_paths), self.bucket, rel_paths[0])
            return f"s3://{self.bucket}/{rel_paths[0]}"
        except (BotoCoreError, ClientError, S3UploadFailedError, OSError) as exc:
            logger.error("snapshot upload failed, keeping local copy: %s", exc)
            return rel_paths[0]

    def url(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ref.startswith("s3://"):
            bucket, _, key = ref[len("s3://"):].partition("/")
            try:
                return self.client.generate_presigned_url(
                    "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=PRESIGN_SECONDS)
            except (BotoCoreError, ClientError) as exc:
                logger.error("presign failed: %s", exc)
                return None
        return LocalSnapshots().url(ref)  # older events stored before switching to S3


def snapshots_from_env() -> LocalSnapshots | S3Snapshots:
    get_settings()  # loads .env
    backend = os.getenv("SNAPSHOT_BACKEND", "local").strip().lower()
    if backend == "s3":
        bucket = os.getenv("S3_BUCKET", "").strip()
        if not bucket:
            raise ValueError("SNAPSHOT_BACKEND=s3 needs S3_BUCKET (run scripts/aws_setup.sh)")
        return S3Snapshots(bucket)
    if backend != "local":
        raise ValueError(f"SNAPSHOT_BACKEND must be 'local' or 's3', not {backend!r}")
    return LocalSnapshots()


_snapshots: LocalSnapshots | S3Snapshots | None = None


def get_snapshots() -> LocalSnapshots | S3Snapshots:
    global _snapshots
    if _snapshots is None:
        _snapshots = snapshots_from_env()
    return _snapshots
