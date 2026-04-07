"""
Whiteout Survival 智能客服 - AgentCore Runtime Agent
使用 Claude Agent SDK (claude-agent-sdk)
"""

import os
import json
import boto3
from typing import Any

from claude_agent_sdk import query, ClaudeAgentOptions, tool, create_sdk_mcp_server, StreamEvent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Config
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID', '')
AGENTCORE_GATEWAY_URL = os.environ.get('AGENTCORE_GATEWAY_URL', '')
REGION = os.environ.get('AWS_REGION_NAME', 'us-west-2')

SYSTEM_PROMPT = """你是 Whiteout Survival（寒霜启示录）的客服助手，专门为玩家提供游戏帮助。

重要规则：
- 玩家的所有问题都是关于 Whiteout Survival 游戏的，包括迁城、移民、募兵、火晶、探险等。
- 对于任何游戏相关问题，你必须首先使用 search_knowledge_base 工具搜索知识库，基于搜索结果回答。
- 不要凭自己的知识回答游戏问题，必须先搜索知识库。
- 如果知识库没有相关内容，如实告知玩家。

请使用玩家的语言回复，保持礼貌和专业。
"""


# Define KB search tool using Claude Agent SDK @tool decorator
@tool(
    "search_knowledge_base",
    "Search the Whiteout Survival game FAQ knowledge base for relevant information. Use this for ANY game-related question.",
    {"query": str}
)
async def search_knowledge_base(args: dict[str, Any]) -> dict[str, Any]:
    query_text = args.get('query', '')
    client = boto3.client('bedrock-agent-runtime', region_name=REGION)
    try:
        response = client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={'text': query_text},
            retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 5}}
        )
        results = [r['content']['text'] for r in response.get('retrievalResults', [])
                   if r.get('content', {}).get('text')]
        text = '\n\n'.join(results) if results else 'No relevant information found in knowledge base.'
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Knowledge base query failed: {e}"}], "isError": True}


# Create MCP server wrapping our KB tool
kb_server = create_sdk_mcp_server(
    name="game_kb",
    version="1.0.0",
    tools=[search_knowledge_base]
)

# Initialize BedrockAgentCoreApp
app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload):
    """Handle agent invocation using Claude Agent SDK"""
    user_message = payload.get('prompt', payload.get('message', ''))
    if not user_message:
        yield {'type': 'error', 'content': 'Missing prompt/message'}
        return

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"game_kb": kb_server},
        allowed_tools=["mcp__game_kb__search_knowledge_base"],
        permission_mode="bypassPermissions",
        max_turns=10,
    )

    async for message in query(prompt=user_message, options=options):
        if isinstance(message, StreamEvent):
            yield message
        else:
            yield message


if __name__ == '__main__':
    app.run()
