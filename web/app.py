"""
Whiteout Survival 智能客服 - Web Service (ECS Fargate)
FastAPI: static files + /chat proxy to AgentCore Runtime with SSE streaming
"""

import os
import json
import logging
import uuid
import boto3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RUNTIME_ARN = os.environ.get('AGENT_RUNTIME_ARN', '')
REGION = os.environ.get('AWS_REGION_NAME', 'us-west-2')
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')

app = FastAPI(title='Whiteout Survival CS Agent')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

agentcore_client = boto3.client('bedrock-agentcore', region_name=REGION)


@app.get('/')
async def index():
    path = '/app/frontend/index.html'
    if os.path.exists(path):
        return HTMLResponse(open(path).read())
    return HTMLResponse('<h1>Whiteout Survival CS Agent</h1>')


@app.get('/config.js')
async def config_js():
    return HTMLResponse(
        content=f"const AWS_CONFIG = {{ userPoolId: '{COGNITO_USER_POOL_ID}', clientId: '{COGNITO_CLIENT_ID}', apiUrl: '' }};",
        media_type='application/javascript'
    )


def parse_runtime_sse(raw_data: str):
    """Parse Runtime SSE events and convert to frontend format.

    Runtime returns Strands-native events like:
      data: {"init_event_loop": true}
      data: {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
      data: {"event": {"contentBlockStart": {"contentBlock": {"toolUse": {"name": "search_kb"}}}}}

    Frontend expects:
      data: {"type": "text", "content": "Hello"}
      data: {"type": "tool_call", "content": {"tool": "search_kb"}}
    """
    for line in raw_data.split('\n'):
        line = line.strip()
        if not line.startswith('data: '):
            continue

        payload = line[6:]
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            # Raw string event from Strands (repr of dict)
            continue

        if not isinstance(data, dict):
            continue

        event = data.get('event', {})

        # Text delta
        if 'contentBlockDelta' in event:
            delta = event['contentBlockDelta'].get('delta', {})
            text = delta.get('text', '')
            if text:
                yield json.dumps({'type': 'text', 'content': text}, ensure_ascii=False)

        # Tool use start (from contentBlockStart.start.toolUse)
        elif 'contentBlockStart' in event:
            start = event['contentBlockStart'].get('start', {})
            if 'toolUse' in start:
                tool_name = start['toolUse'].get('name', 'unknown')
                yield json.dumps({'type': 'tool_call', 'content': {'tool': tool_name, 'input': {}}}, ensure_ascii=False)

        # Tool result (from message-level events)
        elif 'message' in data:
            msg = data['message']
            if isinstance(msg, dict):
                for block in msg.get('content', []):
                    if isinstance(block, dict) and 'toolResult' in block:
                        tr = block['toolResult']
                        result_text = ''
                        for c in tr.get('content', []):
                            if isinstance(c, dict) and 'text' in c:
                                result_text += c['text']
                        if result_text:
                            yield json.dumps({'type': 'tool_result', 'content': result_text[:500]}, ensure_ascii=False)

        # Error
        elif 'error' in data:
            yield json.dumps({'type': 'error', 'content': data['error']}, ensure_ascii=False)

        # Force stop
        elif 'force_stop' in data:
            reason = data.get('force_stop_reason', 'Agent stopped')
            yield json.dumps({'type': 'error', 'content': reason}, ensure_ascii=False)


@app.post('/chat')
async def chat(request: Request):
    try:
        body = await request.json()
        message = body.get('message', '')
        if not message:
            return JSONResponse({'error': 'Missing message'}, status_code=400)

        session_id = body.get('session_id', str(uuid.uuid4()))

        async def stream_agent():
            try:
                response = agentcore_client.invoke_agent_runtime(
                    agentRuntimeArn=RUNTIME_ARN,
                    qualifier='production',
                    runtimeSessionId=session_id,
                    payload=json.dumps({'prompt': message}).encode(),
                )

                # Stream line-by-line from Runtime (true streaming!)
                resp_body = response.get('response', b'')
                buffer = ''
                for chunk in resp_body.iter_chunks():
                    if isinstance(chunk, tuple):
                        chunk = chunk[0]  # (bytes, content_length)
                    text = chunk.decode('utf-8') if isinstance(chunk, bytes) else str(chunk)
                    buffer += text

                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line.startswith('data: '):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except (json.JSONDecodeError, TypeError):
                            continue

                        if not isinstance(data, dict):
                            continue

                        event = data.get('event', {})

                        # Text delta
                        if 'contentBlockDelta' in event:
                            delta = event['contentBlockDelta'].get('delta', {})
                            t = delta.get('text', '')
                            if t:
                                yield f'data: {json.dumps({"type": "text", "content": t}, ensure_ascii=False)}\n\n'

                        # Tool use start
                        elif 'contentBlockStart' in event:
                            start = event['contentBlockStart'].get('start', {})
                            if 'toolUse' in start:
                                tool_name = start['toolUse'].get('name', 'unknown')
                                yield f'data: {json.dumps({"type": "tool_call", "content": {"tool": tool_name, "input": {}}}, ensure_ascii=False)}\n\n'

                        # Tool result (from message-level)
                        elif 'message' in data:
                            msg = data['message']
                            if isinstance(msg, dict):
                                for block in msg.get('content', []):
                                    if isinstance(block, dict) and 'toolResult' in block:
                                        tr = block['toolResult']
                                        result_text = ''
                                        for c in tr.get('content', []):
                                            if isinstance(c, dict) and 'text' in c:
                                                result_text += c['text']
                                        if result_text:
                                            yield f'data: {json.dumps({"type": "tool_result", "content": result_text[:500]}, ensure_ascii=False)}\n\n'

                        # Error
                        elif 'error' in data:
                            yield f'data: {json.dumps({"type": "error", "content": data["error"]}, ensure_ascii=False)}\n\n'

                        # Force stop
                        elif 'force_stop' in data:
                            yield f'data: {json.dumps({"type": "error", "content": data.get("force_stop_reason", "Agent stopped")}, ensure_ascii=False)}\n\n'

                yield 'data: {"type": "done", "content": ""}\n\n'

            except Exception as e:
                logger.error(f'Runtime error: {e}')
                yield f'data: {json.dumps({"type": "error", "content": str(e)})}\n\n'
                yield 'data: {"type": "done", "content": ""}\n\n'

        return StreamingResponse(
            stream_agent(),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    except Exception as e:
        logger.error(f'Chat error: {e}')
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/health')
async def health():
    return {'status': 'healthy'}


# Static files (CSS, JS) - mounted last so routes take priority
from fastapi.staticfiles import StaticFiles
app.mount('/', StaticFiles(directory='/app/frontend', html=False), name='static')
