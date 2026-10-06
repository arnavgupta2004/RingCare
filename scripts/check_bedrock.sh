#!/usr/bin/env bash
# Checks whether this AWS account can invoke Bedrock at all (tiny Amazon Nova Lite call),
# then whether the configured BEDROCK_MODEL_ID works. Costs a fraction of a cent.
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
MODEL="${BEDROCK_MODEL_ID:-$(grep -E '^BEDROCK_MODEL_ID=' "$(dirname "$0")/../.env" 2>/dev/null | cut -d= -f2-)}"
MODEL="${MODEL:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"
MSG='[{"role":"user","content":[{"text":"Reply with the word OK"}]}]'

check() {
  local label="$1" model="$2" out
  if out=$(aws bedrock-runtime converse --region "$REGION" --model-id "$model" \
      --messages "$MSG" --inference-config maxTokens=20 \
      --query 'output.message.content[0].text' --output text 2>&1); then
    echo "OK    $label ($model): $out"
    return 0
  fi
  echo "FAIL  $label ($model): $(echo "$out" | tail -1)"
  return 1
}

echo "Region: $REGION   Identity: $(aws sts get-caller-identity --query Arn --output text 2>&1 | tail -1)"
check "account (Nova Lite)" "us.amazon.nova-lite-v1:0"; account=$?
check "configured model    " "$MODEL"; model=$?

if [ $account -ne 0 ]; then
  echo "-> The account cannot invoke any Bedrock model. Check billing/payment verification or open an AWS Support case."
elif [ $model -ne 0 ]; then
  echo "-> Bedrock works but $MODEL is not enabled. Request access in Bedrock -> Model catalog, or set BEDROCK_MODEL_ID."
fi
exit $(( account || model ))
