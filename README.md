# 游戏智能客服 Demo - Whiteout Survival（寒霜启示录）

> **Fork 说明**: 本仓库 fork 自 [f1shb0t/game-cs-agent](https://github.com/f1shb0t/game-cs-agent)，重构为 AgentCore Runtime + ECS Fargate 架构，支持真流式 SSE 输出。旧版 Lambda 架构保留在 `lambda-legacy` 分支。

基于 AWS Bedrock AgentCore Runtime 和 Strands Agent 的游戏客服 AI Agent 演示项目。

## 🎯 项目概述

本项目展示如何使用 AWS 服务构建一个智能游戏客服系统，具备以下特性：

- **真流式输出**: AgentCore Runtime 原生 SSE streaming，文字逐 token 实时展示
- **智能对话**: Claude Haiku 4.5 模型 (Global Inference Profile)
- **知识库检索**: Bedrock Knowledge Base + Cohere Embed Multilingual V3（支持 100+ 语言跨语言匹配）
- **MCP 工具**: AgentCore Gateway 标准化工具调用（充值查询等）
- **Web 服务**: ECS Fargate (FastAPI) 托管前端 + API 代理
- **负载均衡**: ALB（安全组限制仅 CloudFront 访问）
- **身份认证**: Cognito User Pool
- **一键部署**: CDK + Docker，约 20 分钟

## 📐 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户浏览器                                │
│                    (HTML/JavaScript/CSS)                         │
└────────────────┬────────────────────────────────────────────────┘
                 │ HTTPS
                 ↓
┌────────────────────────────────────────────────────────────────┐
│              CloudFront（或其他 CDN / 直连）                      │
└────────────────┬───────────────────────────────────────────────┘
                 │ CloudFront Prefix List
                 ↓
┌────────────────────────────────────────────────────────────────┐
│              ALB (安全组限制仅 CloudFront IP)                     │
└────────────────┬───────────────────────────────────────────────┘
                 │
                 ↓
┌────────────────────────────────────────────────────────────────┐
│        ECS Fargate (FastAPI Web Service)                        │
│          - 静态前端文件服务                                       │
│          - /chat API → AgentCore Runtime (真流式 SSE)            │
│          - /config.js 动态生成（不再丢失！）                       │
│          - /health 健康检查                                      │
└───────┬────────────────────────────────────────────────────────┘
        │ IAM SigV4
        ↓
┌────────────────────────────────────────────────────────────────┐
│        AgentCore Runtime (Strands Agent)                        │
│          - Claude Haiku 4.5 via Bedrock                         │
│          - 真流式 token-by-token SSE                             │
│          - Session 隔离 (microVM)                                │
│          - 自动扩缩容                                            │
└───────┬────────────┬───────────────────────────────────────────┘
        │            │
        ↓            ↓
┌──────────┐  ┌────────────────────────┐
│ Bedrock  │  │ AgentCore Gateway      │
│ Knowledge│  │  (MCP Endpoint)        │
│   Base   │  │  - IAM Authorizer      │
└─────┬────┘  └────────┬───────────────┘
      │                │
      ↓                ↓
┌─────────┐   ┌──────────────────┐
│ S3 Docs │   │  Lambda (MCP)    │
│ + AOSS  │   │  Recharge Query  │
└─────────┘   │  → DynamoDB      │
              └──────────────────┘
```

### 与旧架构的对比

| 组件 | 旧架构 (`lambda-legacy`) | 新架构 (`main`) |
|------|--------------------------|-----------------|
| 前端托管 | S3 + CloudFront (OAI) | **ECS Fargate (FastAPI)** |
| API 层 | API Gateway REST | **ALB → Fargate** |
| Agent 计算 | Lambda (PythonFunction) | **AgentCore Runtime** |
| 流式输出 | 不支持（一次性返回） | **真流式 SSE (token-by-token)** |
| config.js | S3 手动上传（易丢失） | **Fargate 动态生成** |

## 📁 项目结构

```
game-cs-agent/
├── cdk/                          # CDK 基础设施代码
│   ├── bin/app.ts                # CDK 应用入口
│   ├── lib/game-cs-stack.ts      # 主 Stack 定义
│   └── package.json
├── runtime/                      # AgentCore Runtime Agent
│   ├── main.py                   # Strands Agent (KB + MCP 工具)
│   ├── requirements.txt          # Python 依赖
│   └── Dockerfile                # arm64 容器镜像
├── web/                          # ECS Fargate Web 服务
│   ├── app.py                    # FastAPI (前端 + /chat 代理)
│   ├── requirements.txt
│   └── Dockerfile
├── lambda/
│   ├── create-kb/                # Knowledge Base 创建 (Custom Resource)
│   ├── recharge-query/           # 充值查询 Lambda (MCP 工具)
│   └── seed-data/                # DynamoDB 测试数据初始化
├── frontend/                     # 前端静态文件
│   ├── index.html
│   ├── app.js
│   └── style.css
├── knowledge-base/               # 知识库文档
│   └── game-faq.md               # Whiteout Survival FAQ (30 条)
├── deploy.sh                     # 一键部署脚本
└── cleanup.sh                    # 资源清理脚本
```

## 🚀 快速开始

### 前置条件

1. **AWS 账号** + 配置好的 AWS CLI
2. **Node.js** 18+
3. **Docker**（用于构建 arm64 Runtime 镜像和 Fargate Web 镜像）
4. **Docker Buildx**（arm64 交叉编译）

### 部署步骤

> **重要**: 请使用 `deploy.sh` 一键部署。

```bash
# 1. 克隆项目
git clone https://github.com/HanqingAWS/game-cs-agent.git
cd game-cs-agent

# 2. 构建 Runtime arm64 镜像并推送到 ECR（首次部署需要）
# 安装 QEMU 支持 arm64 交叉编译
sudo docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx create --use --name multiarch --platform linux/amd64,linux/arm64

# 构建并推送
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-west-2
aws ecr create-repository --repository-name game-cs-runtime --region $REGION 2>/dev/null
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
docker buildx build --platform linux/arm64 --load -t game-cs-runtime:latest -f runtime/Dockerfile runtime/
docker tag game-cs-runtime:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/game-cs-runtime:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/game-cs-runtime:latest

# 3. 一键部署
CDK_DEPLOY_REGION=us-west-2 ./deploy.sh
```

### 部署时间

预计部署时间：**约 20 分钟**

- VPC + ALB + ECS Cluster (~5 分钟)
- AgentCore Runtime 创建 (~2 分钟)
- OpenSearch Serverless Collection (~8 分钟)
- Knowledge Base + 数据同步 (~3 分钟)
- CloudFront Distribution (~2 分钟)

### 部署后输出

```
📱 前端地址:
   CloudFront: https://xxx.cloudfront.net
   ALB 直连:   http://xxx.elb.amazonaws.com

🔐 测试账号:
   邮箱: testuser@example.com
   密码: TestUser123!

🏗️ 架构: CloudFront → ALB → ECS Fargate → AgentCore Runtime
   ✅ 真流式 SSE 输出
   ✅ config.js 动态生成
```

## 🧪 测试

### 测试知识库检索
```
Q: 如何迁城到联盟领地？
Q: 如何移民到其他王国？
Q: 什么时候可以解锁扫荡功能？
Q: 火晶建筑有什么不同？
```

### 测试工具调用
```
Q: 查询 player_001 的充值记录
Q: player_003 充值了多少钱？
```

### 观察 Agent 工作流程

聊天界面中展开 "Agent 工作流程" 可以看到：
- 🔧 **工具调用**: 调用了哪个工具，参数是什么
- ✅ **工具结果**: 知识库返回的内容
- 文字逐 token 实时流式输出

## 💰 成本估算

| 服务 | 说明 | 预估月成本 |
|------|------|-----------|
| ECS Fargate | 0.25 vCPU + 0.5GB, 1 任务 | $9 |
| ALB | 按请求 + LCU | $18 |
| AgentCore Runtime | 按 session 时长 | $5-15 |
| Bedrock Claude | 推理按 token 计费 | $15 |
| Bedrock KB | 检索按请求 | $2 |
| OpenSearch Serverless | 2 OCU (search) | $7 |
| CloudFront | 1GB 传输 | $0.12 |
| DynamoDB | 按需 | $0.50 |

**总计**: 约 **$55-65/月**（中等使用量）

## 🔧 自定义

### 换成其他游戏

只需修改 4 个文件：
1. `knowledge-base/game-faq.md` — 替换 FAQ 内容
2. `runtime/main.py` — 修改 `SYSTEM_PROMPT`
3. `lambda/seed-data/index.py` — 修改测试充值数据
4. `frontend/index.html` — 修改标题和快捷问题

### 更新 Runtime 代码

```bash
# 修改 runtime/main.py 后
docker buildx build --platform linux/arm64 --load -t game-cs-runtime:v2 -f runtime/Dockerfile runtime/
docker tag game-cs-runtime:v2 ACCOUNT.dkr.ecr.REGION.amazonaws.com/game-cs-runtime:v2
docker push ACCOUNT.dkr.ecr.REGION.amazonaws.com/game-cs-runtime:v2
# 更新 CDK 中的 tag 并 deploy
```

## 🧹 清理资源

```bash
./cleanup.sh
```

## 📚 分支说明

| 分支 | 说明 |
|------|------|
| `main` | **当前版本** — AgentCore Runtime + ECS Fargate 架构 |
| `lambda-legacy` | 旧版本 — Lambda + API Gateway 架构 |
| `agentcore` | 开发分支（已合并到 main） |

## 👥 作者

原始项目: [f1shb0t/game-cs-agent](https://github.com/f1shb0t/game-cs-agent)

---

**免责声明**: 本项目仅用于演示和学习目的。生产环境使用前，请进行充分的安全审查和性能测试。AWS 服务使用会产生费用，请注意成本控制。
