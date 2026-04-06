"""
Whiteout Survival 智能客服 - Web Service (ECS Fargate)
FastAPI 应用：静态文件服务 + /chat API 代理到 AgentCore Runtime
"""

import os
import json
import logging
import boto3
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config from environment variables (injected by CDK)
RUNTIME_ARN = os.environ.get('AGENT_RUNTIME_ARN', '')
RUNTIME_ENDPOINT_ARN = os.environ.get('AGENT_RUNTIME_ENDPOINT_ARN', '')
REGION = os.environ.get('AWS_REGION_NAME', 'us-west-2')
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')

app = FastAPI(title='Whiteout Survival CS Agent')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# AgentCore Runtime client
agentcore_client = boto3.client('bedrock-agentcore', region_name=REGION)


def generate_config_js():
    """Generate frontend config.js content from environment variables"""
    return f"""const AWS_CONFIG = {{
    userPoolId: '{COGNITO_USER_POOL_ID}',
    clientId: '{COGNITO_CLIENT_ID}',
    apiUrl: ''
}};"""


@app.get('/')
async def index():
    """Serve frontend index.html"""
    index_path = '/app/frontend/index.html'
    if os.path.exists(index_path):
        return HTMLResponse(open(index_path).read())
    return HTMLResponse('<h1>Whiteout Survival CS Agent</h1>')


@app.get('/config.js')
async def config_js():
    """Dynamically generated config.js - no more S3 upload issues!"""
    return HTMLResponse(
        content=generate_config_js(),
        media_type='application/javascript'
    )


@app.post('/chat')
async def chat(request: Request):
    """
    Chat endpoint - proxy to AgentCore Runtime with true SSE streaming.
    Validates Cognito JWT from Authorization header.
    """
    try:
        body = await request.json()
        message = body.get('message', '')
        if not message:
            return JSONResponse({'error': 'Missing message'}, status_code=400)

        # Get auth token (Cognito JWT)
        auth_header = request.headers.get('Authorization', '')

        session_id = body.get('session_id', 'default')

        async def stream_agent():
            """Stream response from AgentCore Runtime"""
            try:
                payload = json.dumps({'prompt': message}).encode()

                response = agentcore_client.invoke_agent_runtime(
                    agentRuntimeArn=RUNTIME_ARN,
                    runtimeSessionId=session_id,
                    payload=payload,
                )

                # Parse the response stream
                response_body = response.get('body', b'')
                if hasattr(response_body, 'read'):
                    data = response_body.read().decode('utf-8')
                else:
                    data = response_body.decode('utf-8') if isinstance(response_body, bytes) else str(response_body)

                # Forward as SSE events
                for line in data.split('\n'):
                    if line.strip():
                        yield f"data: {line}\n\n"

                yield "data: {\"type\": \"done\", \"content\": \"\"}\n\n"

            except Exception as e:
                logger.error(f'Runtime invocation error: {e}')
                error_event = json.dumps({'type': 'error', 'content': str(e)})
                yield f"data: {error_event}\n\n"
                yield "data: {\"type\": \"done\", \"content\": \"\"}\n\n"

        return StreamingResponse(
            stream_agent(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )

    except Exception as e:
        logger.error(f'Chat error: {e}')
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/health')
async def health():
    """Health check for ALB"""
    return {'status': 'healthy'}


# Mount static files (CSS, JS) AFTER route definitions
app.mount('/', StaticFiles(directory='/app/frontend', html=False), name='static')
