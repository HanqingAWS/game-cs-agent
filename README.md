# 游戏智能客服 Demo - 星际征途

> **Fork 说明**: 本仓库 fork 自 [f1shb0t/game-cs-agent](https://github.com/f1shb0t/game-cs-agent)，修复了原始 CDK 代码中 Bedrock Knowledge Base 自动创建部分的多个 bug，支持一键部署到任意 AWS 区域。

基于 AWS Bedrock 和 Strands Agent 的游戏客服 AI Agent 演示项目。

## 🎯 项目概述

本项目展示如何使用 AWS 服务构建一个智能游戏客服系统，具备以下特性：

- **智能对话**: 使用 Claude Haiku 4.5 模型 (Global Inference Profile)，提供自然流畅的对话体验
- **知识库检索**: 集成 Bedrock Knowledge Base，快速查询游戏 FAQ
- **AgentCore Gateway**: 使用 AWS Bedrock AgentCore Gateway，通过 MCP (Model Context Protocol) 标准化工具调用
- **IAM 授权**: Gateway 使用 AWS IAM SigV4 签名进行服务间安全认证
- **流式响应**: 实时展示 Agent 思考过程和工具调用
- **身份认证**: 使用 Cognito 保护用户数据
- **云原生**: 完全 Serverless 架构，按需付费

## 📐 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户浏览器                                 │
│                    (React/HTML/JavaScript)                       │
└────────────────┬────────────────────────────────────────────────┘
                 │ HTTPS
                 ↓
┌────────────────────────────────────────────────────────────────┐
│                   CloudFront + S3                               │
│                   (静态网站托管)                                  │
└────────────────┬───────────────────────────────────────────────┘
                 │
                 │ Cognito JWT Auth
                 ↓
┌────────────────────────────────────────────────────────────────┐
│              API Gateway (REST, Streaming)                      │
│                  POST /chat                                     │
└────────────────┬───────────────────────────────────────────────┘
                 │ Lambda Invoke
                 ↓
┌────────────────────────────────────────────────────────────────┐
│              Lambda (Strands Agent)                             │
│         - Claude Haiku 4.5 via Bedrock (Global)                           │
│         - Knowledge Base Retrieval                              │
│         - MCP Client (IAM Auth)                                 │
└───────┬────────────┬────────────────────────────────────────────┘
        │            │
        │            │                   ┌─────────────────────────┐
        ↓            ↓                   │  AWS IAM SigV4 Auth     │
┌──────────┐  ┌───────────┐             ↓                         │
│ Bedrock  │  │ Bedrock   │     ┌────────────────────────┐        │
│   LLM    │  │ Knowledge │     │ AgentCore Gateway      │←───────┘
│          │  │   Base    │     │  (MCP Endpoint)        │
└──────────┘  └─────┬─────┘     │  - IAM Authorizer      │
                    │           │  - Tool Orchestration  │
                    ↓           └────────┬───────────────┘
              ┌─────────┐                │ Lambda Target Invoke
              │ S3 Docs │                ↓
              └─────────┘       ┌─────────────────────────┐
                                │  Lambda (MCP Tool)      │
                                │  Recharge Query         │
                                └────────┬────────────────┘
                                         │
                                         ↓
                                  ┌──────────────┐
                                  │  DynamoDB    │
                                  │ (充值记录)    │
                                  └──────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Cognito User Pool                             │
│              (前端用户认证 & API Gateway 授权)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 🔧 AgentCore Gateway 说明

本项目使用 **AWS Bedrock AgentCore Gateway** 作为 MCP (Model Context Protocol) 工具的标准化入口：

#### 核心特性
- **MCP 协议**: 符合 Model Context Protocol 标准，提供统一的工具调用接口
- **IAM 授权**: 使用 AWS IAM SigV4 签名进行服务间认证，安全可靠
- **Lambda 目标**: 将 Lambda 函数暴露为 MCP 工具，自动处理协议转换
- **工具编排**: Gateway 自动管理工具调用的路由和响应

#### 认证模式
当前使用 **IAM 授权** 模式：
- Agent Lambda 使用 IAM 角色的临时凭证
- 通过 SigV4 签名对请求进行认证
- Gateway 验证签名并授权访问

#### 可选: Cognito JWT 授权
CDK 代码中包含使用 Cognito JWT 授权的注释示例：
- Gateway 可配置为验证 Cognito User Pool 的 JWT token
- Agent 从 API Gateway 提取用户的 JWT token
- 使用用户身份调用 Gateway，实现端到端的用户上下文传递

#### 工具定义
在 CDK 中通过 `toolSchema` 定义 MCP 工具：
```typescript
toolSchema: {
  tools: [{
    name: 'query_player_recharge',
    description: '查询玩家的充值历史记录',
    inputSchema: {
      type: 'object',
      properties: {
        player_id: { type: 'string', description: '玩家ID' },
        start_date: { type: 'string', description: '开始日期（可选）' },
        end_date: { type: 'string', description: '结束日期（可选）' },
      },
      required: ['player_id'],
    },
  }],
}
```

## 🏗️ 技术栈

### 前端
- HTML5 / CSS3 / JavaScript (原生，无框架依赖)
- Amazon Cognito Identity SDK
- Server-Sent Events (SSE) 流式传输

### 后端
- **语言**: Python 3.12
- **框架**: Strands Agents SDK
- **AI 模型**: Claude Haiku 4.5 (global.anthropic.claude-haiku-4-5-20251001-v1:0)
- **工具协议**: MCP (Model Context Protocol)

### AWS 服务
- **计算**: AWS Lambda
- **API**: Amazon API Gateway (REST with Streaming)
- **AI 网关**: AWS Bedrock AgentCore Gateway (MCP 工具编排)
- **认证**: Amazon Cognito (用户认证), AWS IAM (服务间认证)
- **AI**: Amazon Bedrock (Claude + Knowledge Base + Cohere Embed Multilingual V3)
- **存储**: Amazon S3, Amazon DynamoDB
- **CDN**: Amazon CloudFront
- **日志**: Amazon CloudWatch Logs
- **IaC**: AWS CDK (TypeScript)

## 📁 项目结构

```
game-cs-agent/
├── cdk/                          # CDK 基础设施代码
│   ├── bin/
│   │   └── app.ts                # CDK 应用入口
│   ├── lib/
│   │   └── game-cs-stack.ts      # 主 Stack 定义
│   ├── package.json
│   ├── tsconfig.json
│   └── cdk.json
├── lambda/
│   ├── agent/                    # Strands Agent Lambda
│   │   ├── index.py              # Agent 主逻辑
│   │   └── requirements.txt
│   ├── create-kb/                # Knowledge Base 创建 Lambda (Custom Resource)
│   │   └── index.py              # AOSS Index + KB + DataSource 创建
│   ├── recharge-query/           # 充值查询 Lambda (MCP 工具)
│   │   ├── index.py
│   │   └── requirements.txt
│   └── seed-data/                # DynamoDB 数据初始化
│       ├── index.py
│       └── requirements.txt
├── frontend/                     # 前端静态文件
│   ├── index.html                # 主页面
│   ├── app.js                    # 应用逻辑
│   ├── style.css                 # 样式表
│   └── config.template.js        # 配置模板
├── knowledge-base/               # 知识库文档
│   └── game-faq.md               # 游戏 FAQ (26 个问题)
├── ARCHITECTURE.md               # 架构详细说明
├── README.md                     # 本文件
├── deploy.sh                     # 一键部署脚本
└── cleanup.sh                    # 资源清理脚本
```

## 🚀 快速开始

### 前置条件

1. **AWS 账号**: 需要有有效的 AWS 账号
2. **AWS CLI**: 已安装并配置凭证
   ```bash
   aws configure
   ```
3. **Node.js**: 版本 18.x 或更高
   ```bash
   node --version  # 应该 >= 18.0.0
   ```
4. **权限要求**: IAM 用户需要以下权限：
   - CloudFormation 完全访问
   - Lambda 完全访问
   - API Gateway 完全访问
   - Cognito 完全访问
   - Bedrock 完全访问
   - S3 完全访问
   - DynamoDB 完全访问
   - CloudFront 完全访问
   - IAM 角色创建权限

### 部署步骤

> **重要**: 请使用 `deploy.sh` 一键部署。该脚本会自动完成所有配置，包括生成前端所需的 `config.js` 文件。手动执行 `cdk deploy` 不会生成此文件，会导致前端无法正常工作。

```bash
# 1. 克隆项目
git clone https://github.com/HanqingAWS/game-cs-agent.git
cd game-cs-agent

# 2. 一键部署
./deploy.sh
```

部署脚本会自动完成以下操作：
- ✅ 检查必要工具 (Node.js, npm, AWS CLI)
- ✅ 安装 CDK 依赖
- ✅ Bootstrap CDK (首次使用)
- ✅ 部署所有 AWS 资源 (Lambda, API Gateway, Cognito, Bedrock KB, AOSS, CloudFront 等)
- ✅ 创建测试用户
- ✅ 初始化 DynamoDB 测试数据
- ✅ 生成前端配置文件 (`config.js`)
- ✅ 输出访问地址和测试账号

### 部署时间

预计部署时间：**约 15 分钟**

部署过程包括：
- Lambda 函数打包和部署 (~2 分钟)
- OpenSearch Serverless Collection 创建 (~8 分钟)
- Bedrock Knowledge Base 创建和数据导入 (~3 分钟)
- CloudFront 分发创建 (~2 分钟)
- 其他资源创建 (~2 分钟)

### 部署后的输出

部署成功后，您会看到类似输出：

```
===================================
部署成功！
===================================

📱 前端地址:
   https://d1234567890abc.cloudfront.net

🔐 测试账号:
   邮箱: testuser@example.com
   密码: TestUser123!

🔑 AWS 配置:
   User Pool ID: us-east-1_XXXXXXXXX
   Client ID: XXXXXXXXXXXXXXXXXXXXXXXXXX
   API URL: https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/prod

📝 注意事项:
   1. 首次访问前端可能需要等待 CloudFront 缓存更新（5-10分钟）
   2. 如果遇到问题，请查看 CloudWatch Logs
   3. 测试账号已自动创建
```

## 🧪 测试步骤

### 1. 访问前端

在浏览器中打开 CloudFront URL。

### 2. 登录

使用测试账号登录：
- **邮箱**: testuser@example.com
- **密码**: TestUser123!

### 3. 测试对话

尝试以下问题：

#### 测试知识库检索
```
Q: 什么是星际征途？
Q: 如何升级星舰？
Q: 支持哪些充值方式？
Q: 游戏卡顿怎么办？
```

#### 测试工具调用
```
Q: 查询 player_001 的充值记录
Q: 帮我看看 player_002 最近的充值
Q: player_003 充值了多少钱？
```

#### 测试复合能力
```
Q: 我充值后没到账怎么办？请先查询我的充值记录，玩家ID是 player_001
```

### 4. 观察 Agent 工作流程

在聊天界面中，您可以展开 "🔍 Agent 工作流程" 查看：
- 💭 **思考过程**: Agent 如何分析问题
- 🔧 **工具调用**: 调用了哪些工具，参数是什么
- ✅ **工具结果**: 工具返回的数据
- 📝 **最终回复**: Agent 基于信息合成的回答

## 📊 测试数据说明

### 玩家充值记录

系统预置了 5 个测试玩家的充值数据：

| 玩家 ID | 玩家名称 | 充值次数 | 总金额 (CNY) |
|---------|---------|---------|-------------|
| player_001 | 星际探险家 | 5 | 382 |
| player_002 | 宇宙商人 | 6 | 1,208 |
| player_003 | 银河舰长 | 3 | 326 |
| player_004 | 星际指挥官 | 8 | 1,455 |
| player_005 | 深空旅者 | 5 | 660 |

### 知识库内容

包含 26 个游戏常见问题，涵盖：
- 游戏基础 (4 个)
- 账号相关 (4 个)
- 充值与付费 (4 个)
- 游戏玩法 (4 个)
- 技术问题 (3 个)
- 活动与奖励 (3 个)
- 社区与客服 (3 个)

## 💰 成本估算

基于 AWS 按需付费模式，预估月成本（假设中等使用量）：

| 服务 | 用量 | 预估成本 |
|------|------|---------|
| **Lambda** | 10,000 次调用/月，平均 3GB·秒 | $1.00 |
| **API Gateway** | 10,000 次请求/月 | $0.04 |
| **Bedrock Claude Sonnet** | 1M input tokens, 500K output tokens | $15.00 |
| **Bedrock Knowledge Base** | 10,000 次查询 | $2.00 |
| **DynamoDB** | 按需模式，低流量 | $0.50 |
| **S3** | 1GB 存储 + 少量请求 | $0.05 |
| **CloudFront** | 1GB 传输 | $0.12 |
| **Cognito** | 1,000 MAU (免费额度内) | $0.00 |
| **CloudWatch Logs** | 1GB 日志 | $0.50 |

**总计**: 约 **$19-20/月**

### 成本优化建议

1. **使用 Lambda 预留容量**: 高频使用场景可降低 30% 成本
2. **启用 CloudFront 缓存**: 减少 API 调用次数
3. **DynamoDB On-Demand**: 低流量时比预置容量更经济
4. **日志保留策略**: 设置 CloudWatch Logs 自动过期（7天）
5. **开发环境**: 使用 `cdk destroy` 及时清理未使用资源

### 免费额度

AWS 免费套餐包括：
- Lambda: 100万次请求/月
- API Gateway: 100万次调用/月（前 12 个月）
- DynamoDB: 25GB 存储
- S3: 5GB 存储（前 12 个月）
- CloudFront: 1TB 传输（前 12 个月）

**注意**: Bedrock 没有免费额度，所有 AI 模型调用均计费。

## 🔧 自定义配置

### 修改 AI 模型

编辑 `lambda/agent/index.py`:

```python
MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0'  # 改为其他模型
```

可选模型：
- `anthropic.claude-3-5-sonnet-20241022-v2:0` (Sonnet 3.5)
- `anthropic.claude-opus-4-20250514` (Opus 4，更强大但更贵)
- `anthropic.claude-haiku-3-5-20241022-v1:0` (Haiku 3.5，更快更便宜)

### 修改系统提示词

编辑 `lambda/agent/index.py`:

```python
SYSTEM_PROMPT = """
你是一个游戏客服助手...
"""
```

### 添加更多知识

编辑 `knowledge-base/game-faq.md`，添加更多 Q&A。

部署后，Bedrock Knowledge Base 会自动同步更新。

### 修改 DynamoDB 测试数据

编辑 `lambda/seed-data/index.py` 中的 `SEED_DATA` 变量。

## 🛠️ 故障排查

### 前端无法访问

**问题**: 访问 CloudFront URL 显示 403 Forbidden

**解决**:
1. 等待 5-10 分钟，CloudFront 分发需要时间生效
2. 检查 S3 bucket policy 是否正确配置
3. 清除浏览器缓存

### 登录失败

**问题**: 提示 "Login failed" 或 "User does not exist"

**解决**:
1. 确认使用正确的测试账号（testuser@example.com）
2. 检查 `frontend/config.js` 中的 User Pool ID 和 Client ID 是否正确
3. 在 AWS Console → Cognito → User Pools 中确认用户存在

### Agent 无响应

**问题**: 发送消息后没有回复

**解决**:
1. 打开浏览器开发者工具，查看 Network 和 Console 错误
2. 检查 CloudWatch Logs → `/aws/lambda/GameCsAgentStack-AgentFunction...`
3. 确认 Bedrock 模型 ID 正确且有访问权限
4. 检查 Lambda 环境变量是否正确配置

### 知识库查询失败

**问题**: Agent 无法检索知识库内容

**解决**:
1. 确认 Bedrock Knowledge Base 创建成功
2. 检查 S3 bucket 中是否有 `documents/game-faq.md` 文件
3. 在 AWS Console → Bedrock → Knowledge Bases 中手动触发数据源同步

### MCP 工具调用失败

**问题**: 无法查询充值记录

**解决**:
1. 检查 recharge-query Lambda 函数日志
2. 确认 DynamoDB 表 `PlayerRechargeRecords` 中有数据
3. 验证 Lambda 函数 URL 配置正确

## 🧹 清理资源

### 使用脚本清理

```bash
./cleanup.sh
```

输入 `yes` 确认删除。

### 手动清理

```bash
cd cdk
npx cdk destroy
```

### 彻底清理

如果需要删除 CDK Bootstrap 资源：

```bash
aws cloudformation delete-stack --stack-name CDKToolkit --region us-west-2  # 替换为你的部署区域
```

**注意**: 删除 CDKToolkit 会影响同区域的其他 CDK 项目。

## 📚 扩展阅读

### 相关文档

- [AWS Bedrock 文档](https://docs.aws.amazon.com/bedrock/)
- [Strands Agents SDK](https://github.com/anthropics/strands)
- [MCP Protocol](https://modelcontextprotocol.io/)
- [AWS CDK 文档](https://docs.aws.amazon.com/cdk/)
- [Amazon Cognito 文档](https://docs.aws.amazon.com/cognito/)

### 后续优化方向

1. **增强 Agent 能力**
   - 添加更多 MCP 工具（账号查询、订单管理等）
   - 实现多轮对话记忆
   - 集成语音输入/输出

2. **提升用户体验**
   - 添加打字机效果
   - 支持 Markdown 渲染
   - 添加消息历史记录
   - 实现多语言支持

3. **生产就绪改进**
   - 添加速率限制
   - 实现会话管理
   - 集成监控告警
   - 添加 A/B 测试
   - 实现反馈收集

4. **成本优化**
   - 使用 Lambda 预留容量
   - 实现智能缓存策略
   - 根据流量选择合适的 AI 模型

## 🔨 Fork 修复记录

本 Fork 修复了原始仓库中 Bedrock Knowledge Base CDK 部署的以下问题：

| # | 问题 | 原因 | 修复方案 |
|---|------|------|---------|
| 1 | AOSS Collection ARN 无法解析 | inline Lambda 中使用 `${AWS::Region}` CloudFormation 语法 | 改为 CDK 原生 `AWS::OpenSearchServerless::Collection` 资源 |
| 2 | iam:PassRole 权限缺失 | CreateKbFunctionRole 未授权 PassRole | 添加 inline policy |
| 3 | AOSS Collection 不存在 | CDK 中未创建 Collection 资源 | 添加 Collection + 加密/网络/访问策略 |
| 4 | cfnresponse 模块缺失 | `Code.fromAsset` 不自动包含 cfnresponse | 在 Lambda 中自行实现 CFN response |
| 5 | AOSS 403 Forbidden | urllib3 手动 SigV4 签名不兼容 AOSS | 改用 `opensearch-py` + `requests-aws4auth` |
| 6 | cr.Provider 返回值格式错误 | Lambda 直接发 HTTP response 而非返回 dict | 修改为返回 `{PhysicalResourceId, Data}` dict |
| 7 | 区域硬编码 | `app.ts` 固定 us-east-1 | 改为 `CDK_DEPLOY_REGION` 环境变量，支持任意区域 |
| 8 | CloudFront 403 | `websiteIndexDocument` 使 S3Origin 走 website endpoint，不支持 OAI | 去掉 `websiteIndexDocument`，由 CloudFront `defaultRootObject` 处理 |
| 9 | Agent Lambda 缺少依赖 | `Code.fromAsset` 不安装 requirements.txt | 改为 `PythonFunction`（Docker 自动打包） |
| 10 | `strands-tools` 包不存在 | requirements.txt 中包名错误 | 移除，已被 `strands-agents-tools` 覆盖 |
| 11 | `mcp_proxy_for_aws` 模块不存在 | Agent 代码引用了 strands-agents-tools 中不存在的模块 | 改用 `streamablehttp_client` + 自定义 `SigV4HttpxAuth` |
| 12 | Bedrock ConverseStream 权限缺失 | IAM 只授权了 InvokeModel，Strands SDK 使用 Converse API | 添加 `Converse`/`ConverseStream` 权限 + global inference profile ARN |
| 13 | deploy.sh 区域硬编码 | 所有 AWS CLI 调用写死 us-east-1 | 支持 `CDK_DEPLOY_REGION` 环境变量 |
| 14 | deploy.sh config.js 未上传 | 只生成本地文件，未上传到 S3 | 自动上传到 S3 并清除 CloudFront 缓存 |

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 👥 作者

原始项目: [f1shb0t/game-cs-agent](https://github.com/f1shb0t/game-cs-agent)

## 📧 联系方式

如有问题，请提交 GitHub Issue。

---

**免责声明**: 本项目仅用于演示和学习目的。生产环境使用前，请进行充分的安全审查和性能测试。AWS 服务使用会产生费用，请注意成本控制。
