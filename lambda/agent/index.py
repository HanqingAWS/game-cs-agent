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
try:
    from strands.tools.mcp import MCPClient
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    # 如果 strands 还没有内置 MCP 支持，使用独立的 MCP 客户端
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client


# 环境变量配置
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID')
AGENTCORE_GATEWAY_URL = os.environ.get('AGENTCORE_GATEWAY_URL')
MODEL_ID = 'anthropic.claude-sonnet-4-20250514'
REGION = 'us-east-1'

# 系统提示词
SYSTEM_PROMPT = """你是一个游戏客服助手，专门为"星际征途"游戏的玩家提供服务。

你的职责包括：
1. 回答游戏相关问题（通过知识库工具）
2. 查询玩家充值记录（通过查询工具）
3. 解决玩家遇到的问题
4. 提供友好、专业的客户服务

请始终用中文回复，保持礼貌和专业。如果不确定答案，请如实告知玩家，不要编造信息。
"""


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
    import boto3

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

        # 提取检索结果
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
    """创建 AgentCore Gateway MCP 客户端"""
    # 使用 streamablehttp_client 连接到 AgentCore Gateway
    client = MCPClient(
        lambda: streamablehttp_client(AGENTCORE_GATEWAY_URL)
    )
    return client


# 创建 Agent 实例
def create_agent():
    """创建配置好的 Strands Agent"""

    # Bedrock Claude 模型
    model = BedrockModel(
        model_id=MODEL_ID,
        region=REGION
    )

    # 工具列表
    tools = [search_knowledge_base]

    # 如果配置了 AgentCore Gateway，添加 MCP 工具
    if AGENTCORE_GATEWAY_URL:
        try:
            mcp_client = create_mcp_client()
            tools.append(mcp_client)
        except Exception as e:
            print(f'警告: MCP 客户端初始化失败: {e}')

    # 创建 Agent
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools
    )

    return agent


async def stream_agent_response(agent: Agent, message: str, user_id: str = None) -> AsyncGenerator[Dict[str, Any], None]:
    """
    流式运行 agent 并生成事件

    Args:
        agent: Strands Agent 实例
        message: 用户消息
        user_id: 用户 ID（可选）

    Yields:
        事件字典，包含 type 和 content
    """

    try:
        # 流式运行 agent
        async for event in agent.run_stream(message):

            # 根据事件类型格式化输出
            if hasattr(event, 'type'):
                event_type = event.type

                if event_type == 'thinking':
                    # Agent 思考过程
                    yield {
                        'type': 'thinking',
                        'content': event.content if hasattr(event, 'content') else str(event)
                    }

                elif event_type == 'tool_call':
                    # 工具调用
                    tool_name = event.tool_name if hasattr(event, 'tool_name') else 'unknown'
                    tool_input = event.tool_input if hasattr(event, 'tool_input') else {}

                    yield {
                        'type': 'tool_call',
                        'content': {
                            'tool': tool_name,
                            'input': tool_input
                        }
                    }

                elif event_type == 'tool_result':
                    # 工具结果
                    yield {
                        'type': 'tool_result',
                        'content': event.result if hasattr(event, 'result') else str(event)
                    }

                elif event_type == 'text':
                    # 最终文本输出
                    yield {
                        'type': 'text',
                        'content': event.content if hasattr(event, 'content') else str(event)
                    }

                else:
                    # 其他类型事件
                    yield {
                        'type': 'info',
                        'content': str(event)
                    }
            else:
                # 如果是简单字符串响应
                if isinstance(event, str):
                    yield {
                        'type': 'text',
                        'content': event
                    }

    except Exception as e:
        yield {
            'type': 'error',
            'content': f'Agent 运行错误: {str(e)}'
        }

    # 发送完成事件
    yield {
        'type': 'done',
        'content': ''
    }


# Lambda 响应流式处理器
def lambda_handler(event, context):
    """
    Lambda 主处理函数
    支持响应流式传输（Lambda Response Streaming）
    """

    print(f'收到请求: {json.dumps(event)}')

    # 解析请求体
    try:
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})

        message = body.get('message', '')
        user_id = body.get('user_id', 'anonymous')

        if not message:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': '缺少消息内容'})
            }

    except Exception as e:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'请求解析失败: {str(e)}'})
        }

    # 创建 agent
    agent = create_agent()

    # 检查是否支持响应流
    if hasattr(context, 'response_stream'):
        # Lambda Response Streaming 模式
        return stream_response(agent, message, user_id, context.response_stream)
    else:
        # 非流式模式（降级）
        return sync_response(agent, message, user_id)


def stream_response(agent, message, user_id, response_stream):
    """Lambda Response Streaming 模式处理"""

    async def write_events():
        async for event in stream_agent_response(agent, message, user_id):
            # 格式化为 SSE 事件
            event_data = json.dumps(event, ensure_ascii=False)
            sse_event = f'data: {event_data}\n\n'

            # 写入响应流
            response_stream.write(sse_event.encode('utf-8'))
            await asyncio.sleep(0)  # 让出控制权

    # 设置响应头
    response_stream.set_content_type('text/event-stream')

    # 运行异步事件流
    asyncio.run(write_events())


def sync_response(agent, message, user_id):
    """非流式模式（降级）"""

    try:
        # 同步运行 agent
        result = asyncio.run(agent.run(message))

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'type': 'text',
                'content': result
            }, ensure_ascii=False)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Agent 运行失败: {str(e)}'
            })
        }
