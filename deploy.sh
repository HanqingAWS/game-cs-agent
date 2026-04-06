#!/bin/bash

# 游戏智能客服 Demo - 一键部署脚本

set -e

echo "====================================="
echo "游戏智能客服 Demo - 开始部署"
echo "====================================="

# 部署区域（可通过环境变量 CDK_DEPLOY_REGION 覆盖，默认 us-east-1）
DEPLOY_REGION="${CDK_DEPLOY_REGION:-us-east-1}"
export CDK_DEPLOY_REGION="$DEPLOY_REGION"

echo "部署区域: ${DEPLOY_REGION}"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 检查必要的工具
echo ""
echo -e "${YELLOW}检查必要工具...${NC}"

if ! command -v node &> /dev/null; then
    echo -e "${RED}错误: 未安装 Node.js${NC}"
    echo "请访问 https://nodejs.org/ 下载安装"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo -e "${RED}错误: 未安装 npm${NC}"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo -e "${RED}错误: 未安装 AWS CLI${NC}"
    echo "请访问 https://aws.amazon.com/cli/ 下载安装"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: 未安装 Docker (PythonFunction 需要 Docker 打包 Lambda 依赖)${NC}"
    exit 1
fi

echo -e "${GREEN}✓ 工具检查完成${NC}"

# 检查 AWS 凭证
echo ""
echo -e "${YELLOW}检查 AWS 凭证...${NC}"

if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}错误: AWS 凭证未配置${NC}"
    echo "请运行: aws configure"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓ AWS 账号: ${ACCOUNT_ID}${NC}"

# 进入 CDK 目录
cd cdk

# 安装依赖
echo ""
echo -e "${YELLOW}安装 CDK 依赖...${NC}"
npm install

echo -e "${GREEN}✓ 依赖安装完成${NC}"

# 编译 TypeScript
echo ""
echo -e "${YELLOW}编译 TypeScript...${NC}"
npm run build

echo -e "${GREEN}✓ 编译完成${NC}"

# Bootstrap CDK（如果需要）
echo ""
echo -e "${YELLOW}检查 CDK Bootstrap...${NC}"

if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region ${DEPLOY_REGION} &> /dev/null; then
    echo "首次使用 CDK，正在进行 Bootstrap..."
    npx cdk bootstrap aws://${ACCOUNT_ID}/${DEPLOY_REGION}
    echo -e "${GREEN}✓ Bootstrap 完成${NC}"
else
    echo -e "${GREEN}✓ CDK 已 Bootstrap${NC}"
fi

# 部署 Stack
echo ""
echo -e "${YELLOW}部署 CDK Stack...${NC}"
echo "这可能需要约 15 分钟，请耐心等待..."
echo ""

npx cdk deploy --require-approval never

# 获取输出
echo ""
echo -e "${YELLOW}获取部署输出...${NC}"

CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack \
    --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
    --output text)

USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack \
    --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
    --output text)

CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack \
    --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
    --output text)

API_URL=$(aws cloudformation describe-stacks \
    --stack-name GameCsAgentStack \
    --region ${DEPLOY_REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
    --output text)

# 找到 S3 Website Bucket (filter by resource type to avoid matching custom resources)
WEBSITE_BUCKET=$(aws cloudformation describe-stack-resources \
    --stack-name GameCsAgentStack \
    --region ${DEPLOY_REGION} \
    --query 'StackResources[?ResourceType==`AWS::S3::Bucket` && starts_with(LogicalResourceId, `WebsiteBucket`)].PhysicalResourceId' \
    --output text)

# 创建前端配置文件并上传到 S3
echo ""
echo -e "${YELLOW}创建并上传前端配置文件...${NC}"

cat > /tmp/config.js <<EOF
// AWS 配置 - 由 deploy.sh 自动生成
const AWS_CONFIG = {
    userPoolId: '${USER_POOL_ID}',
    clientId: '${CLIENT_ID}',
    apiUrl: '${API_URL}'
};
EOF

aws s3 cp /tmp/config.js s3://${WEBSITE_BUCKET}/config.js --region ${DEPLOY_REGION}

# 清除 CloudFront 缓存
DIST_ID=$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?contains(Origins.Items[0].DomainName, '${WEBSITE_BUCKET}')].Id" \
    --output text 2>/dev/null)

if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
    aws cloudfront create-invalidation --distribution-id ${DIST_ID} --paths '/*' > /dev/null 2>&1
    echo -e "${GREEN}✓ CloudFront 缓存已清除${NC}"
fi

echo -e "${GREEN}✓ 配置文件已上传${NC}"

# 回到根目录
cd ..

# 输出部署信息
echo ""
echo "====================================="
echo -e "${GREEN}部署成功！${NC}"
echo "====================================="
echo ""
echo "📱 前端地址:"
echo "   ${CLOUDFRONT_URL}"
echo ""
echo "🔐 测试账号:"
echo "   邮箱: testuser@example.com"
echo "   密码: TestUser123!"
echo ""
echo "🔑 AWS 配置:"
echo "   User Pool ID: ${USER_POOL_ID}"
echo "   Client ID: ${CLIENT_ID}"
echo "   API URL: ${API_URL}"
echo ""
echo "📝 注意事项:"
echo "   1. 首次访问前端可能需要等待 1-2 分钟（CloudFront 缓存更新）"
echo "   2. 如果遇到问题，请查看 CloudWatch Logs"
echo "   3. 测试账号已自动创建"
echo ""
echo "🧹 清理资源:"
echo "   运行 ./cleanup.sh 可删除所有资源"
echo ""
echo "====================================="
