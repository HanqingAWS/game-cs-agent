#!/bin/bash

set -euo pipefail

REGION="${CDK_DEPLOY_REGION:-us-west-2}"
STACK_NAME="${STACK_NAME:-GameCsAgentStack}"
SCOPE_NAME="${SCOPE_NAME:-strands.telemetry.tracer}"
SPAN_LOG_GROUP="${SPAN_LOG_GROUP:-aws/spans}"

stack_output() {
    local output_key="$1"
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs[?OutputKey==\`${output_key}\`].OutputValue | [0]" \
        --output text
}

RUNTIME_ARN="${AGENT_RUNTIME_ARN:-$(stack_output AgentRuntimeArn)}"
RUNTIME_ID="${RUNTIME_ARN##*/}"
RUNTIME_VERSION="$(
    aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$REGION" \
        --query agentRuntimeVersion \
        --output text
)"
LIVE_VERSION="$(
    aws bedrock-agentcore-control get-agent-runtime-endpoint \
        --agent-runtime-id "$RUNTIME_ID" \
        --endpoint-name production \
        --region "$REGION" \
        --query liveVersion \
        --output text
)"

if [ "$LIVE_VERSION" != "$RUNTIME_VERSION" ]; then
    echo "Production endpoint serves version $LIVE_VERSION, expected $RUNTIME_VERSION" >&2
    exit 1
fi

TRACE_DESTINATION="$(
    aws xray get-trace-segment-destination \
        --region "$REGION" \
        --query '[Destination,Status]' \
        --output text
)"
if [ "$TRACE_DESTINATION" != $'CloudWatchLogs\tACTIVE' ]; then
    echo "CloudWatch Transaction Search is not active: $TRACE_DESTINATION" >&2
    exit 1
fi

aws logs describe-log-groups \
    --region "$REGION" \
    --log-group-name-prefix "$SPAN_LOG_GROUP" \
    --query "logGroups[?logGroupName==\`${SPAN_LOG_GROUP}\`].logGroupName | [0]" \
    --output text | grep -qx "$SPAN_LOG_GROUP" || {
    echo "Span log group does not exist: $SPAN_LOG_GROUP" >&2
    exit 1
}

START_TIME_MS="$((($(date +%s) - 30) * 1000))"
SESSION_ID="observability-check-$(date +%s)-$(printf '%08x' "$RANDOM")"
RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

echo "Invoking production endpoint for observability verification..."
aws bedrock-agentcore invoke-agent-runtime \
    --agent-runtime-arn "$RUNTIME_ARN" \
    --qualifier production \
    --runtime-session-id "$SESSION_ID" \
    --content-type application/json \
    --accept application/json \
    --cli-binary-format raw-in-base64-out \
    --payload '{"prompt":"Hello. Reply with one short greeting."}' \
    --region "$REGION" \
    "$RESPONSE_FILE" >/dev/null

grep -q '^data:' "$RESPONSE_FILE" || {
    echo "Runtime invocation did not return an SSE response" >&2
    exit 1
}

for _ in $(seq 1 24); do
    SPAN_COUNT="$(
        aws logs filter-log-events \
            --region "$REGION" \
            --log-group-name "$SPAN_LOG_GROUP" \
            --start-time "$START_TIME_MS" \
            --filter-pattern "\"${SCOPE_NAME}\"" \
            --no-paginate \
            --query 'length(events)' \
            --output text
    )"
    if [ "$SPAN_COUNT" -gt 0 ]; then
        echo "Verified scope.name=${SCOPE_NAME} in ${SPAN_LOG_GROUP}"
        echo "Runtime version $RUNTIME_VERSION is live on production"
        exit 0
    fi
    sleep 10
done

echo "No ${SCOPE_NAME} spans arrived in ${SPAN_LOG_GROUP}" >&2
exit 1
