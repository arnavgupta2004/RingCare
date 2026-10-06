#!/usr/bin/env bash
# Delete everything aws_setup.sh created (table, bucket + contents, topic + subscriptions)
# and switch .env back to local backends.
#
#   ./scripts/aws_teardown.sh [--yes] [--dry-run]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
YES=0; DRY=0
for a in "$@"; do
  case "$a" in --yes) YES=1 ;; --dry-run) DRY=1 ;; *) echo "unknown option: $a" >&2; exit 2 ;; esac
done

envget() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
REGION="$(envget AWS_REGION)"; REGION="${REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TABLE="$(envget DYNAMODB_TABLE)"; TABLE="${TABLE:-doorsight-state}"
BUCKET="$(envget S3_BUCKET)"; BUCKET="${BUCKET:-doorsight-snapshots-$ACCOUNT}"
TOPIC_ARN="$(envget SNS_TOPIC_ARN)"; TOPIC_ARN="${TOPIC_ARN:-arn:aws:sns:$REGION:$ACCOUNT:doorsight-caregiver-alerts}"

run() { if [ "$DRY" = 1 ]; then echo "  would run: $*"; else "$@"; fi; }
WILL=""; [ "$DRY" = 1 ] && WILL="would be "  # wording for dry runs

[ "$DRY" = 1 ] && echo "DRY RUN — nothing will be deleted."
echo "This deletes, in account $ACCOUNT ($REGION):"
echo "  DynamoDB table $TABLE (all doorstep state)"
echo "  S3 bucket $BUCKET and every snapshot in it"
echo "  SNS topic $TOPIC_ARN and its subscriptions"
if [ "$DRY" = 0 ] && [ "$YES" = 0 ]; then
  read -r -p "Type 'delete' to continue: " answer
  [ "$answer" = "delete" ] || { echo "aborted"; exit 1; }
fi

if aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null 2>&1; then
  run aws dynamodb delete-table --region "$REGION" --table-name "$TABLE" --output text --query TableDescription.TableStatus
  [ "$DRY" = 1 ] || aws dynamodb wait table-not-exists --region "$REGION" --table-name "$TABLE"
  echo "DynamoDB: table $TABLE ${WILL}deleted"
else
  echo "DynamoDB: table $TABLE not found"
fi

if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  run aws s3 rm "s3://$BUCKET" --recursive --quiet
  run aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION"
  echo "S3: bucket $BUCKET ${WILL}emptied and deleted"
else
  echo "S3: bucket $BUCKET not found"
fi

if aws sns get-topic-attributes --region "$REGION" --topic-arn "$TOPIC_ARN" >/dev/null 2>&1; then
  run aws sns delete-topic --region "$REGION" --topic-arn "$TOPIC_ARN"
  echo "SNS: topic ${WILL}deleted (subscriptions removed with it)"
else
  echo "SNS: topic not found"
fi

if [ "$DRY" = 0 ] && [ -f "$ENV_FILE" ]; then
  python3 - "$ENV_FILE" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
drop = ("DYNAMODB_TABLE=", "S3_BUCKET=", "SNS_TOPIC_ARN=", "STATE_BACKEND=", "SNAPSHOT_BACKEND=")
p.write_text("\n".join(l for l in p.read_text().splitlines() if not l.startswith(drop)) + "\n")
PY
  echo ".env: AWS resource settings removed; back to SQLite, local snapshots and log alerts"
fi
