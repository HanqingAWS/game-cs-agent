#!/bin/bash

# 游戏智能客服 Demo - 清理脚本

set -e

echo "====================================="
echo "游戏智能客服 Demo - 清理资源"
echo "====================================="

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "${YELLOW}警告: 此操作将删除所有部署的资源！${NC}"
echo ""
read -p "确认删除？(yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "取消清理"
    exit 0
fi

echo ""
echo "${YELLOW}开始清理资源...${NC}"

cd cdk

# 删除 Stack
echo ""
echo "${YELLOW}删除 CloudFormation Stack...${NC}"
npx cdk destroy --force

echo ""
echo "${GREEN}✓ 资源清理完成${NC}"
echo ""
echo "注意: CDK Bootstrap Stack (CDKToolkit) 未被删除"
echo "如需完全清理，请手动删除 CDKToolkit Stack"
echo ""
