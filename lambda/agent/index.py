"""
游戏客服 AI Agent Lambda 函数
使用 Strands Agent SDK
集成 Bedrock Knowledge Base 和 AgentCore Gateway MCP 工具
返回 SSE 格式事件流，前端展示 Agent 工作流程
"""

import json
import os
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from strands.tools.mcp import MCPClient
from strands_tools.mcp_client import streamablehttp_client


# Config
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID')
AGENTCORE_GATEWAY_URL = os.environ.get('AGENTCORE_GATEWAY_URL')
REGION = os.environ.get('AWS_REGION_NAME', 'us-east-1')
MODEL_ID = 'global.anthropic.claude-haiku-4-5-20251001-v1:0'

SYSTEM_PROMPT = """你是一个游戏客服助手，专门为"星际征途"游戏的玩家提供服务。

你的职责包括：
1. 回答游戏相关问题（通过知识库工具）
2. 查询玩家充值记录（通过查询工具）
3. 解决玩家遇到的问题
4. 提供友好、专业的客户服务

请始终用中文回复，保持礼貌和专业。如果不确定答案，请如实告知玩家，不要编造信息。
"""


class SigV4HttpxAuth(httpx.Auth):
    """AWS SigV4 authentication for httpx"""
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


class SSECollector:
    """Collect SSE events during Agent execution for workflow display"""

    def __init__(self):
        self.events = []
        self.final_text = ''

    def add_event(self, event_type, content):
        self.events.append({'type': event_type, 'content': content})

    def to_sse_body(self):
        lines = []
        for event in self.events:
            lines.append(f'data: {json.dumps(event, ensure_ascii=False)}')
            lines.append('')
        return '\n'.join(lines)


@tool
def search_knowledge_base(query: str) -> str:
    """在游戏知识库中搜索相关信息

    Args:
        query: 搜索查询文本

    Returns:
        知识库中的相关信息
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
        return '\n\n'.join(results) if results else '未找到相关信息'
    except Exception as e:
        return f'知识库查询失败: {str(e)}'


def create_mcp_client():
    auth = SigV4HttpxAuth(region=REGION, service='bedrock-agentcore')
    return MCPClient(lambda: streamablehttp_client(url=AGENTCORE_GATEWAY_URL, auth=auth))


def create_agent(collector: SSECollector):
    """Create Agent with event collection via hooks"""
    model = BedrockModel(model_id=MODEL_ID, region=REGION)
    tools = [search_knowledge_base]

    if AGENTCORE_GATEWAY_URL:
        try:
            tools.append(create_mcp_client())
        except Exception as e:
            print(f'MCP client init failed: {e}')

    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)
    return agent


def lambda_handler(event, context):
    print('Request received')

    try:
        body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body', {})
        message = body.get('message', '')
        if not message:
            return {'statusCode': 400,
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({'error': '缺少消息内容'})}
    except Exception as e:
        return {'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': f'请求解析失败: {str(e)}'})}

    collector = SSECollector()
    agent = create_agent(collector)

    try:
        # Run agent and collect the result
        result = agent(message)

        # Extract tool use events from the agent's message history
        if hasattr(result, 'messages') or hasattr(agent, 'messages'):
            messages = getattr(result, 'messages', None) or getattr(agent, 'messages', [])
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get('role', '')
                content_list = msg.get('content', [])
                if not isinstance(content_list, list):
                    continue
                for block in content_list:
                    if not isinstance(block, dict):
                        continue
                    # Thinking
                    if 'reasoningContent' in block:
                        thinking_text = block['reasoningContent'].get('reasoningText', {}).get('text', '')
                        if thinking_text:
                            collector.add_event('thinking', thinking_text)
                    # Tool use
                    if 'toolUse' in block:
                        tool_info = block['toolUse']
                        collector.add_event('tool_call', {
                            'tool': tool_info.get('name', 'unknown'),
                            'input': tool_info.get('input', {})
                        })
                    # Tool result
                    if 'toolResult' in block:
                        tr = block['toolResult']
                        result_text = ''
                        for c in tr.get('content', []):
                            if isinstance(c, dict) and 'text' in c:
                                result_text += c['text']
                        if result_text:
                            collector.add_event('tool_result', result_text[:500])

        # Final text response
        response_text = str(result)
        collector.add_event('text', response_text)
        collector.add_event('done', '')

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/event-stream',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache',
            },
            'body': collector.to_sse_body()
        }

    except Exception as e:
        print(f'Agent error: {e}')
        collector.add_event('error', f'Agent 运行错误: {str(e)}')
        collector.add_event('done', '')
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/event-stream',
                'Access-Control-Allow-Origin': '*',
            },
            'body': collector.to_sse_body()
        }
