#!/bin/bash

# Whiteout Survival 智能客服 - 一键部署脚本 (AgentCore Runtime + ECS Fargate 架构)

set -e

echo "====================================="
echo "Whiteout Survival 智能客服 - AgentCore 架构部署"
echo "====================================="

DEPLOY_REGION="${CDK_DEPLOY_REGION:-us-east-1}"
export CDK_DEPLOY_REGION="$DEPLOY_REGION"

echo "部署区域: ${DEPLOY_REGION}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check tools
echo ""
echo -e "${YELLOW}检查必要工具...${NC}"

for cmd in node npm aws docker; do
    if ! command -v $cmd &> /dev/null; then
        echo -e "${RED}错误: 未安装 $cmd${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ 工具检查完成${NC}"

# Check AWS credentials
echo ""
echo -e "${YELLOW}检查 AWS 凭证...${NC}"
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}错误: AWS 凭证未配置${NC}"
    exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓ AWS 账号: ${ACCOUNT_ID}${NC}"

# CDK setup
cd cdk
echo ""
echo -e "${YELLOW}安装 CDK 依赖...${NC}"
npm install
echo -e "${GREEN}✓ 依赖安装完成${NC}"

echo ""
echo -e "${YELLOW}编译 TypeScript...${NC}"
npm run build
echo -e "${GREEN}✓ 编译完成${NC}"

# Bootstrap CDK
echo ""
echo -e "${YELLOW}检查 CDK Bootstrap...${NC}"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region ${DEPLOY_REGION} &> /dev/null; then
    echo "首次使用 CDK，正在进行 Bootstrap..."
    npx cdk bootstrap aws://${ACCOUNT_ID}/${DEPLOY_REGION}
    echo -e "${GREEN}✓ Bootstrap 完成${NC}"
else
    echo -e "${GREEN}✓ CDK 已 Bootstrap${NC}"
fi

# Deploy
echo ""
echo -e "${YELLOW}部署 CDK Stack (AgentCore Runtime + ECS Fargate)...${NC}"
echo "这可能需要约 20 分钟（含 VPC、ALB、ECS、AOSS、KB 创建）..."
echo ""

npx cdk deploy --require-approval never

# Get outputs
echo ""
echo -e "${YELLOW}获取部署输出...${NC}"

CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text)

ALB_URL=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`ALBUrl`].OutputValue' --output text)

USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' --output text)

cd ..

echo ""
echo "====================================="
echo -e "${GREEN}部署成功！${NC}"
echo "====================================="
echo ""
echo "📱 前端地址:"
echo "   CloudFront: ${CLOUDFRONT_URL}"
echo "   ALB 直连:   ${ALB_URL}"
echo ""
echo "🔐 测试账号:"
echo "   邮箱: testuser@example.com"
echo "   密码: TestUser123!"
echo ""
echo "🔑 AWS 配置:"
echo "   User Pool ID: ${USER_POOL_ID}"
echo "   Client ID: ${CLIENT_ID}"
echo ""
echo "🏗️ 架构: CloudFront → ALB → ECS Fargate → AgentCore Runtime"
echo "   ✅ 真流式 SSE 输出"
echo "   ✅ Cognito JWT 认证 (Runtime 内置)"
echo "   ✅ config.js 由 Fargate 动态生成 (不再丢失！)"
echo ""
echo "🧹 清理资源:"
echo "   运行 ./cleanup.sh 可删除所有资源"
echo ""
echo "====================================="
