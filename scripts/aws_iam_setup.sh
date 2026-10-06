#!/usr/bin/env bash
# Create the least-privilege IAM user the DoorSight server runs as, and wire it up as AWS profile
# "doorsight" (~/.aws/credentials) with AWS_PROFILE=doorsight in .env. Idempotent.
#
#   ./scripts/aws_iam_setup.sh          # run with admin credentials (your normal CLI login)
#
# Policy: docs/iam/doorsight-app-policy.json (only the DoorSight table, snapshot objects,
# caregiver topic and the two Bedrock models). The secret key is never printed.
set -euo pipefail
unset AWS_PROFILE  # admin actions use your default credentials, not the app profile

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="doorsight-app"
PROFILE="doorsight"
POLICY_NAME="DoorSightAppAccess"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

POLICY="$(sed -e "s/\${ACCOUNT}/$ACCOUNT/g" -e "s/\${REGION}/$REGION/g" "$ROOT/docs/iam/doorsight-app-policy.json")"

if aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  echo "IAM: user $USER_NAME already exists"
else
  aws iam create-user --user-name "$USER_NAME" --tags Key=Project,Value=DoorSight >/dev/null
  echo "IAM: created user $USER_NAME"
fi
aws iam put-user-policy --user-name "$USER_NAME" --policy-name "$POLICY_NAME" --policy-document "$POLICY"
echo "IAM: inline policy $POLICY_NAME attached"

# Reuse the profile's key if it still belongs to this user; otherwise create one.
EXISTING_KEY="$(aws configure get aws_access_key_id --profile "$PROFILE" 2>/dev/null || true)"
USER_KEYS="$(aws iam list-access-keys --user-name "$USER_NAME" --query 'AccessKeyMetadata[].AccessKeyId' --output text)"
if [ -n "$EXISTING_KEY" ] && echo "$USER_KEYS" | grep -qw "$EXISTING_KEY"; then
  echo "IAM: profile '$PROFILE' already has an active key for $USER_NAME (…${EXISTING_KEY: -4})"
else
  if [ "$(echo "$USER_KEYS" | wc -w)" -ge 2 ]; then
    echo "IAM: $USER_NAME already has 2 access keys; delete one in the IAM console first" >&2
    exit 1
  fi
  read -r KEY_ID SECRET < <(aws iam create-access-key --user-name "$USER_NAME" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
  aws configure set aws_access_key_id "$KEY_ID" --profile "$PROFILE"
  aws configure set aws_secret_access_key "$SECRET" --profile "$PROFILE"
  unset SECRET
  echo "IAM: created access key …${KEY_ID: -4} and saved it as profile '$PROFILE' in ~/.aws/credentials"
fi
aws configure set region "$REGION" --profile "$PROFILE"

python3 - "$ROOT/.env" "$PROFILE" <<'PY'
import sys, pathlib
p, profile = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = [l for l in (p.read_text().splitlines() if p.exists() else []) if not l.startswith("AWS_PROFILE=")]
p.write_text("\n".join(lines + [f"AWS_PROFILE={profile}"]) + "\n")
PY
echo ".env: AWS_PROFILE=$PROFILE (restart the server to use it)"
