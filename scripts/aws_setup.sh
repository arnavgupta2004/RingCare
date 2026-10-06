#!/usr/bin/env bash
# Create DoorSight's AWS resources idempotently (safe to re-run) and record them in .env.
#
#   ./scripts/aws_setup.sh [--email caregiver@example.com] [--enable]
#
#   DynamoDB  table  doorsight-state                  on-demand (PAY_PER_REQUEST), pk/sk, no indexes
#   S3        bucket doorsight-snapshots-<account>    private, SSE-S3, frames expire after 30 days
#   SNS       topic  doorsight-caregiver-alerts       optional email subscription (confirm via email)
#
# --enable also sets STATE_BACKEND=dynamodb and SNAPSHOT_BACKEND=s3 in .env.
# SNS_TOPIC_ARN in .env turns on email delivery of caregiver alerts.
# Nothing here is always-on: on-demand table, pay-per-request S3/SNS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
REGION="${AWS_REGION:-us-east-1}"
EMAIL="${CAREGIVER_EMAIL:-}"
ENABLE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --email) EMAIL="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TABLE="${DYNAMODB_TABLE:-doorsight-state}"
BUCKET="${S3_BUCKET:-doorsight-snapshots-$ACCOUNT}"
TOPIC_NAME="doorsight-caregiver-alerts"
TAGS_KV="Key=Project,Value=DoorSight"

set_env() {  # set_env KEY VALUE: replace or append in .env
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys, pathlib
path, key, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines() if path.exists() else []
lines = [l for l in lines if not l.startswith(f"{key}=")] + [f"{key}={value}"]
path.write_text("\n".join(lines) + "\n")
PY
}

echo "Account $ACCOUNT  region $REGION"

# --- DynamoDB ------------------------------------------------------------------
if aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null 2>&1; then
  echo "DynamoDB: table $TABLE already exists"
else
  aws dynamodb create-table --region "$REGION" --table-name "$TABLE" \
    --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --tags "$TAGS_KV" >/dev/null
  aws dynamodb wait table-exists --region "$REGION" --table-name "$TABLE"
  echo "DynamoDB: created table $TABLE (on-demand)"
fi

# --- S3 --------------------------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "S3: bucket $BUCKET already exists"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  echo "S3: created bucket $BUCKET"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-snapshots","Status":"Enabled","Filter":{"Prefix":"frames/"},"Expiration":{"Days":30}}]}' >/dev/null
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=Project,Value=DoorSight}]'
echo "S3: private (public access blocked), SSE-S3 encrypted, snapshots expire after 30 days"

# --- SNS -------------------------------------------------------------------------
TOPIC_ARN="$(aws sns create-topic --region "$REGION" --name "$TOPIC_NAME" --tags "$TAGS_KV" \
  --query TopicArn --output text)"  # idempotent: returns the existing topic
echo "SNS: topic $TOPIC_ARN"
if [ -n "$EMAIL" ]; then
  EXISTING="$(aws sns list-subscriptions-by-topic --region "$REGION" --topic-arn "$TOPIC_ARN" \
    --query "Subscriptions[?Protocol=='email' && Endpoint=='$EMAIL'].SubscriptionArn | [0]" --output text)"
  if [ "$EXISTING" = "None" ] || [ -z "$EXISTING" ]; then
    aws sns subscribe --region "$REGION" --topic-arn "$TOPIC_ARN" --protocol email \
      --notification-endpoint "$EMAIL" >/dev/null
    echo "SNS: subscribed $EMAIL — confirm the email from AWS Notifications to start receiving alerts"
  else
    echo "SNS: $EMAIL already subscribed ($EXISTING)"
  fi
else
  echo "SNS: no --email given; add a subscriber later with --email"
fi

# --- .env ------------------------------------------------------------------------
set_env AWS_REGION "$REGION"
set_env DYNAMODB_TABLE "$TABLE"
set_env S3_BUCKET "$BUCKET"
set_env SNS_TOPIC_ARN "$TOPIC_ARN"
if [ "$ENABLE" = 1 ]; then
  set_env STATE_BACKEND dynamodb
  set_env SNAPSHOT_BACKEND s3
  echo ".env: resources recorded; STATE_BACKEND=dynamodb SNAPSHOT_BACKEND=s3 enabled (restart the server)"
else
  echo ".env: resources recorded; caregiver alerts now go to SNS. Re-run with --enable to use DynamoDB + S3."
fi
