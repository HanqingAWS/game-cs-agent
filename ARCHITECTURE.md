# 游戏智能客服 Demo - 架构设计

## 整体架构

```
Frontend (S3+CloudFront)  →  API Gateway (REST, streaming)  →  Lambda (Strands Agent)
     ↓ Cognito Auth                                                    ↓
                                                          ┌────────────┼────────────┐
                                                          ↓            ↓            ↓
                                                   Bedrock KB    AgentCore GW   Bedrock LLM
                                                   (知识库)     (MCP工具)      (Claude Haiku 4.5)
                                                                     ↓
                                                              Lambda (查询工具)
                                                                     ↓
                                                              DynamoDB (充值数据)
```

## 组件清单

### 1. Strands Agent (Lambda)
- Python Lambda function，使用 strands-agents SDK
- 模型: Claude Haiku 4.5 via Bedrock
- 工具: Bedrock Knowledge Base retrieval + AgentCore Gateway MCP tool
- Lambda Response Streaming 支持（通过 API Gateway streaming）

### 2. API Gateway (REST)
- Response streaming 模式
- Lambda proxy integration（使用 InvokeWithResponseStreaming）
- Cognito User Pool Authorizer

### 3. Frontend (S3 + CloudFront)
- React/HTML SPA
- Cognito 登录集成
- 展示 agent 工作流程（工具调用、知识库查询等）
- SSE 流式显示回复

### 4. Cognito
- User Pool + App Client
- 用于前端认证
- Token 也用于 AgentCore Gateway 的 inbound auth

### 5. Bedrock Knowledge Base
- 存放游戏FAQ、规则等知识
- 作为 Strands agent 的工具（retrieve tool）
- S3 作为数据源
- Embedding 模型: Cohere Embed Multilingual V3（多语言支持）

### 6. DynamoDB + 查询 Lambda
- DynamoDB 表存放玩家充值记录（模拟数据）
- Lambda function 实现查询逻辑
- 作为 MCP tool 注册到 AgentCore Gateway

### 7. AgentCore Gateway
- 托管 MCP 工具（充值数据查询）
- Lambda target
- Cognito inbound auth（使用前端的 Cognito token）

### 8. CDK
- TypeScript CDK 项目
- 所有资源通过 CDK 部署
- 包含部署文档

## 技术要点

### Strands Agent 流式输出
- 使用 `awslambdaric` + Lambda response streaming
- API Gateway REST API 设置 `responseTransferMode: STREAM`
- 前端通过 SSE/fetch streaming 接收

### AgentCore Gateway + Cognito
- Gateway 使用 Cognito 作为 inbound authorizer
- Agent Lambda 调用 Gateway 时传递用户的 Cognito token
- CDK 使用 `@aws-cdk/aws-bedrock-agentcore-alpha`

### Strands Agent 集成 MCP
```python
from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

# AgentCore Gateway MCP endpoint
mcp_client = MCPClient(
    lambda: streamablehttp_client(gateway_url)
)
```
