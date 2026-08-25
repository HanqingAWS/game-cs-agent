#!/bin/bash

# Whiteout Survival customer-service agent deployment.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_REGION="${CDK_DEPLOY_REGION:-us-west-2}"
STACK_NAME="${STACK_NAME:-GameCsAgentStack}"
RUNTIME_REPOSITORY="${RUNTIME_REPOSITORY:-game-cs-runtime}"
export CDK_DEPLOY_REGION="$DEPLOY_REGION"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() {
    echo -e "${YELLOW}$*${NC}"
}

success() {
    echo -e "${GREEN}[OK] $*${NC}"
}

fail() {
    echo -e "${RED}[ERROR] $*${NC}" >&2
    exit 1
}

stack_output() {
    local output_key="$1"
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$DEPLOY_REGION" \
        --query "Stacks[0].Outputs[?OutputKey==\`${output_key}\`].OutputValue | [0]" \
        --output text
}

echo "====================================="
echo "Whiteout Survival AgentCore deployment"
echo "====================================="
echo "Region: $DEPLOY_REGION"

info "Checking required tools..."
for cmd in node npm aws docker sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || fail "Missing required command: $cmd"
done
docker buildx version >/dev/null 2>&1 || fail "Docker Buildx is required"
success "Required tools are available"

info "Checking AWS credentials..."
aws sts get-caller-identity >/dev/null 2>&1 || fail "AWS credentials are not configured"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
success "Authenticated to AWS account $ACCOUNT_ID"

info "Preparing the Runtime ECR repository..."
if ! aws ecr describe-repositories \
    --repository-names "$RUNTIME_REPOSITORY" \
    --region "$DEPLOY_REGION" >/dev/null 2>&1; then
    aws ecr create-repository \
        --repository-name "$RUNTIME_REPOSITORY" \
        --image-scanning-configuration scanOnPush=true \
        --region "$DEPLOY_REGION" >/dev/null
fi

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${DEPLOY_REGION}.amazonaws.com/${RUNTIME_REPOSITORY}"
RUNTIME_SOURCE_HASH="$(
    cd "$ROOT_DIR/runtime"
    find . -type f -print | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -c1-12
)"
RUNTIME_IMAGE_TAG="${RUNTIME_IMAGE_TAG:-otel-${RUNTIME_SOURCE_HASH}}"

aws ecr get-login-password --region "$DEPLOY_REGION" |
    docker login --username AWS --password-stdin \
        "${ACCOUNT_ID}.dkr.ecr.${DEPLOY_REGION}.amazonaws.com" >/dev/null

info "Preparing the ARM64 Buildx builder..."
if ! docker buildx inspect multiarch >/dev/null 2>&1; then
    docker buildx create \
        --name multiarch \
        --driver docker-container \
        --platform linux/amd64,linux/arm64 \
        --use >/dev/null
else
    docker buildx use multiarch
fi

if ! docker buildx inspect --bootstrap | grep -q 'linux/arm64'; then
    info "Installing ARM64 emulation support..."
    docker run --privileged --rm tonistiigi/binfmt --install arm64 >/dev/null
    docker buildx inspect --bootstrap | grep -q 'linux/arm64' ||
        fail "Buildx does not support linux/arm64"
fi

info "Building and pushing Runtime image ${ECR_URI}:${RUNTIME_IMAGE_TAG}..."
docker buildx build \
    --builder multiarch \
    --platform linux/arm64 \
    --push \
    --tag "${ECR_URI}:${RUNTIME_IMAGE_TAG}" \
    --file "$ROOT_DIR/runtime/Dockerfile" \
    "$ROOT_DIR/runtime"
success "Runtime image pushed"

cd "$ROOT_DIR/cdk"

info "Installing CDK dependencies..."
npm install --no-package-lock --no-audit --no-fund
success "CDK dependencies installed"

info "Compiling TypeScript..."
npm run build
success "TypeScript compiled"

info "Checking CDK bootstrap..."
if ! aws cloudformation describe-stacks \
    --stack-name CDKToolkit \
    --region "$DEPLOY_REGION" >/dev/null 2>&1; then
    npx cdk bootstrap "aws://${ACCOUNT_ID}/${DEPLOY_REGION}"
fi
success "CDK bootstrap is ready"

info "Synthesizing and reviewing the CDK change set..."
npx cdk synth -c "runtimeImageTag=${RUNTIME_IMAGE_TAG}" >/dev/null
npx cdk diff -c "runtimeImageTag=${RUNTIME_IMAGE_TAG}"

info "Deploying ${STACK_NAME}..."
npx cdk deploy \
    --require-approval never \
    -c "runtimeImageTag=${RUNTIME_IMAGE_TAG}"
success "CloudFormation deployment completed"

AGENT_RUNTIME_ARN="$(stack_output AgentRuntimeArn)"
RUNTIME_ID="${AGENT_RUNTIME_ARN##*/}"
RUNTIME_VERSION="$(
    aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region "$DEPLOY_REGION" \
        --query agentRuntimeVersion \
        --output text
)"

info "Promoting Runtime version ${RUNTIME_VERSION} to the production endpoint..."
LIVE_VERSION="$(
    aws bedrock-agentcore-control get-agent-runtime-endpoint \
        --agent-runtime-id "$RUNTIME_ID" \
        --endpoint-name production \
        --region "$DEPLOY_REGION" \
        --query liveVersion \
        --output text
)"

if [ "$LIVE_VERSION" != "$RUNTIME_VERSION" ]; then
    aws bedrock-agentcore-control update-agent-runtime-endpoint \
        --agent-runtime-id "$RUNTIME_ID" \
        --endpoint-name production \
        --agent-runtime-version "$RUNTIME_VERSION" \
        --region "$DEPLOY_REGION" >/dev/null
fi

for _ in $(seq 1 60); do
    ENDPOINT_STATE="$(
        aws bedrock-agentcore-control get-agent-runtime-endpoint \
            --agent-runtime-id "$RUNTIME_ID" \
            --endpoint-name production \
            --region "$DEPLOY_REGION" \
            --query '[status,liveVersion]' \
            --output text
    )"
    ENDPOINT_STATUS="$(printf '%s' "$ENDPOINT_STATE" | awk '{print $1}')"
    LIVE_VERSION="$(printf '%s' "$ENDPOINT_STATE" | awk '{print $2}')"

    if [ "$ENDPOINT_STATUS" = "READY" ] && [ "$LIVE_VERSION" = "$RUNTIME_VERSION" ]; then
        break
    fi
    if [[ "$ENDPOINT_STATUS" == *FAILED ]]; then
        fail "Production endpoint update failed with status $ENDPOINT_STATUS"
    fi
    sleep 10
done

[ "$ENDPOINT_STATUS" = "READY" ] && [ "$LIVE_VERSION" = "$RUNTIME_VERSION" ] ||
    fail "Production endpoint did not become ready on version $RUNTIME_VERSION"
success "Production endpoint now serves Runtime version $RUNTIME_VERSION"

cd "$ROOT_DIR"
AGENT_RUNTIME_ARN="$AGENT_RUNTIME_ARN" \
    CDK_DEPLOY_REGION="$DEPLOY_REGION" \
    STACK_NAME="$STACK_NAME" \
    "$ROOT_DIR/scripts/verify-agentcore-observability.sh"

CLOUDFRONT_URL="$(stack_output CloudFrontURL)"
ALB_URL="$(stack_output ALBUrl)"
USER_POOL_ID="$(stack_output UserPoolId)"
CLIENT_ID="$(stack_output UserPoolClientId)"

echo ""
echo "====================================="
success "Deployment and observability verification completed"
echo "====================================="
echo "CloudFront: $CLOUDFRONT_URL"
echo "ALB:        $ALB_URL"
echo "User Pool:  $USER_POOL_ID"
echo "Client ID:  $CLIENT_ID"
echo "Runtime:    $AGENT_RUNTIME_ARN"
echo "Version:    $RUNTIME_VERSION"
echo "Image tag:  $RUNTIME_IMAGE_TAG"
