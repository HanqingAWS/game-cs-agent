"""
Whiteout Survival 智能客服 - AgentCore Runtime Agent
部署到 Bedrock AgentCore Runtime，支持真流式 SSE 输出
"""

import os
import json
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from strands.tools.mcp import MCPClient
from strands_tools.mcp_client import streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Config from environment variables
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID', '')
AGENTCORE_GATEWAY_URL = os.environ.get('AGENTCORE_GATEWAY_URL', '')
REGION = os.environ.get('AWS_REGION_NAME', 'us-west-2')
MODEL_ID = os.environ.get('MODEL_ID', 'global.anthropic.claude-haiku-4-5-20251001-v1:0')

SYSTEM_PROMPT = """你是 Whiteout Survival（寒霜启示录）的客服助手，专门为玩家提供游戏帮助。

重要规则：
- 玩家的所有问题都是关于 Whiteout Survival 游戏的，包括迁城、移民、募兵、火晶、探险等。
- 对于任何游戏相关问题，你必须首先使用 search_knowledge_base 工具搜索知识库，基于搜索结果回答。
- 不要凭自己的知识回答游戏问题，必须先搜索知识库。
- 如果知识库没有相关内容，如实告知玩家。
- 如果玩家要查充值记录，使用充值查询工具。

请使用玩家的语言回复，保持礼貌和专业。
"""


class SigV4HttpxAuth(httpx.Auth):
    """AWS SigV4 authentication for httpx (MCP Gateway access)"""
    def __init__(self, region, service):
        self.region = region
        self.service = service

    def auth_flow(self, request):
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        aws_req = AWSRequest(method=request.method, url=str(request.url),
                             data=request.content, headers=dict(request.headers))
        SigV4Auth(creds, self.service, self.region).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v
        yield request


@tool
def search_knowledge_base(query: str) -> str:
    """Search the game FAQ knowledge base for relevant information.

    Args:
        query: Search query text

    Returns:
        Relevant information from the knowledge base
    """
    client = boto3.client('bedrock-agent-runtime', region_name=REGION)
    try:
        response = client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={'text': query},
            retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 5}}
        )
        results = [r['content']['text'] for r in response.get('retrievalResults', [])
                   if r.get('content', {}).get('text')]
        return '\n\n'.join(results) if results else 'No relevant information found'
    except Exception as e:
        return f'Knowledge base query failed: {str(e)}'


def create_tools():
    """Create tool list: KB search + MCP tools"""
    tools = [search_knowledge_base]
    if AGENTCORE_GATEWAY_URL:
        try:
            auth = SigV4HttpxAuth(region=REGION, service='bedrock-agentcore')
            mcp = MCPClient(lambda: streamablehttp_client(url=AGENTCORE_GATEWAY_URL, auth=auth))
            tools.append(mcp)
        except Exception as e:
            print(f'MCP client init failed: {e}')
    return tools


# Initialize app only (lightweight)
app = BedrockAgentCoreApp()

# Lazy-init agent on first invocation to avoid 30s init timeout
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        model = BedrockModel(model_id=MODEL_ID, region=REGION)
        tools = create_tools()
        _agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)
    return _agent


@app.entrypoint
async def invoke(payload):
    """Handle agent invocation with true streaming output"""
    user_message = payload.get('prompt', payload.get('message', ''))
    if not user_message:
        yield {'type': 'error', 'content': 'Missing prompt/message in payload'}
        return

    agent = get_agent()
    stream = agent.stream_async(user_message)
    async for event in stream:
        yield event


if __name__ == '__main__':
    app.run()
