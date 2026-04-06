"""
游戏客服 AI Agent Lambda 函数
使用 Strands Agent SDK，支持响应流式传输
集成 Bedrock Knowledge Base 和 AgentCore Gateway MCP 工具
"""

import json
import os
import asyncio
from typing import AsyncGenerator, Dict, Any

# Strands Agent SDK
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

# MCP 客户端
from strands.tools.mcp import MCPClient
from strands_tools.mcp_client import streamablehttp_client

# AWS SigV4 auth for httpx
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


# 环境变量配置
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID')
AGENTCORE_GATEWAY_URL = os.environ.get('AGENTCORE_GATEWAY_URL')
REGION = os.environ.get('AWS_REGION_NAME', 'us-east-1')
MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0'

# 系统提示词
SYSTEM_PROMPT = """你是一个游戏客服助手，专门为"星际征途"游戏的玩家提供服务。

你的职责包括：
1. 回答游戏相关问题（通过知识库工具）
2. 查询玩家充值记录（通过查询工具）
3. 解决玩家遇到的问题
4. 提供友好、专业的客户服务

请始终用中文回复，保持礼貌和专业。如果不确定答案，请如实告知玩家，不要编造信息。
"""


class SigV4HttpxAuth(httpx.Auth):
    """AWS SigV4 authentication for httpx (used by MCP streamablehttp_client)"""

    def __init__(self, region: str, service: str):
        self.region = region
        self.service = service

    def auth_flow(self, request: httpx.Request):
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(creds, self.service, self.region).add_auth(aws_req)
        for key, val in aws_req.headers.items():
            request.headers[key] = val
        yield request


# Bedrock Knowledge Base 检索工具
@tool
def search_knowledge_base(query: str) -> str:
    """
    在游戏知识库中搜索相关信息

    Args:
        query: 搜索查询文本

    Returns:
        知识库中的相关信息
    """
    client = boto3.client('bedrock-agent-runtime', region_name=REGION)

    try:
        response = client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={
                'text': query
            },
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': 5
                }
            }
        )

        results = []
        for result in response.get('retrievalResults', []):
            content = result.get('content', {}).get('text', '')
            if content:
                results.append(content)

        if results:
            return '\n\n'.join(results)
        else:
            return '未找到相关信息'

    except Exception as e:
        return f'知识库查询失败: {str(e)}'


# 创建 MCP 客户端用于充值查询
def create_mcp_client():
    """创建 AgentCore Gateway MCP 客户端 (AWS IAM SigV4 认证)"""
    auth = SigV4HttpxAuth(region=REGION, service='bedrock-agentcore')
    mcp_factory = lambda: streamablehttp_client(
        url=AGENTCORE_GATEWAY_URL,
        auth=auth,
    )
    return MCPClient(mcp_factory)


# 创建 Agent 实例
def create_agent():
    """创建配置好的 Strands Agent"""
    model = BedrockModel(model_id=MODEL_ID, region=REGION)
    tools = [search_knowledge_base]

    if AGENTCORE_GATEWAY_URL:
        try:
            mcp_client = create_mcp_client()
            tools.append(mcp_client)
        except Exception as e:
            print(f'警告: MCP 客户端初始化失败: {e}')

    return Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)


# Lambda 主处理函数
def lambda_handler(event, context):
    print(f'收到请求')

    try:
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})

        message = body.get('message', '')
        if not message:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': '缺少消息内容'})
            }

    except Exception as e:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': f'请求解析失败: {str(e)}'})
        }

    agent = create_agent()

    try:
        result = agent(message)
        response_text = str(result)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'type': 'text',
                'content': response_text
            }, ensure_ascii=False)
        }

    except Exception as e:
        print(f'Agent 运行失败: {e}')
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': f'Agent 运行失败: {str(e)}'})
        }
