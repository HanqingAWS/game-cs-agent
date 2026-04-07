// 配置 - 这些值将在部署时通过 CDK 输出获取
// 部署后需要更新这些值
const CONFIG = {
    userPoolId: 'YOUR_USER_POOL_ID',
    clientId: 'YOUR_CLIENT_ID',
    apiUrl: 'YOUR_API_URL'
};

// 尝试从 config.js 加载配置（如果存在）
try {
    if (typeof AWS_CONFIG !== 'undefined') {
        Object.assign(CONFIG, AWS_CONFIG);
    }
} catch (e) {
    console.log('config.js not found, using default config');
}

// Cognito 相关
let userPool;
let cognitoUser;
let idToken;

// 初始化 Cognito User Pool
function initCognito() {
    const poolData = {
        UserPoolId: CONFIG.userPoolId,
        ClientId: CONFIG.clientId
    };
    userPool = new AmazonCognitoIdentity.CognitoUserPool(poolData);
}

// 检查用户是否已登录
function checkAuth() {
    initCognito();
    cognitoUser = userPool.getCurrentUser();

    if (cognitoUser != null) {
        cognitoUser.getSession((err, session) => {
            if (err) {
                console.error('Session error:', err);
                showLogin();
                return;
            }

            if (session.isValid()) {
                idToken = session.getIdToken().getJwtToken();
                const email = session.getIdToken().payload.email;
                showChat(email);
            } else {
                showLogin();
            }
        });
    } else {
        showLogin();
    }
}

// 登录
function login() {
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    const errorDiv = document.getElementById('loginError');

    if (!email || !password) {
        errorDiv.textContent = '请输入邮箱和密码';
        return;
    }

    errorDiv.textContent = '';

    const authenticationData = {
        Username: email,
        Password: password,
    };

    const authenticationDetails = new AmazonCognitoIdentity.AuthenticationDetails(authenticationData);

    const userData = {
        Username: email,
        Pool: userPool
    };

    cognitoUser = new AmazonCognitoIdentity.CognitoUser(userData);

    cognitoUser.authenticateUser(authenticationDetails, {
        onSuccess: (result) => {
            idToken = result.getIdToken().getJwtToken();
            showChat(email);
        },
        onFailure: (err) => {
            console.error('Login error:', err);
            errorDiv.textContent = '登录失败: ' + (err.message || '未知错误');
        },
    });
}

// 登出
function logout() {
    if (cognitoUser) {
        cognitoUser.signOut();
    }
    showLogin();
}

// 显示登录界面
function showLogin() {
    document.getElementById('loginContainer').classList.remove('hidden');
    document.getElementById('chatContainer').classList.add('hidden');
    document.getElementById('loginEmail').value = '';
    document.getElementById('loginPassword').value = '';
    document.getElementById('loginError').textContent = '';
}

// 显示聊天界面
function showChat(email) {
    document.getElementById('loginContainer').classList.add('hidden');
    document.getElementById('chatContainer').classList.remove('hidden');
    document.getElementById('userEmail').textContent = email;
}

// 发送消息
async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();

    if (!message) return;

    // 禁用输入
    input.disabled = true;
    document.getElementById('sendBtn').disabled = true;

    // 显示用户消息
    addMessage('user', message);

    // 清空输入框
    input.value = '';

    // 创建 assistant 消息容器
    const assistantMessage = createAssistantMessage();

    try {
        // 调用 API（流式）
        await streamChat(message, assistantMessage);
    } catch (error) {
        console.error('Chat error:', error);
        assistantMessage.querySelector('.message-content').innerHTML +=
            `<p style="color: red;">错误: ${error.message}</p>`;
    } finally {
        // 重新启用输入
        input.disabled = false;
        document.getElementById('sendBtn').disabled = false;
        input.focus();
    }
}

// 快速发送预设问题
function quickSend(message) {
    document.getElementById('messageInput').value = message;
    sendMessage();
}

// 处理键盘事件
function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// 添加消息到聊天界面
function addMessage(role, content) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    if (typeof content === 'string') {
        contentDiv.innerHTML = formatMessage(content);
    } else {
        contentDiv.appendChild(content);
    }

    messageDiv.appendChild(contentDiv);
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    return messageDiv;
}

// 创建 assistant 消息容器
function createAssistantMessage() {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message assistant';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = '<div class="thinking-indicator">正在思考...</div>';

    messageDiv.appendChild(contentDiv);
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    return messageDiv;
}

// 格式化消息内容
function formatMessage(text) {
    // 简单的 Markdown 风格格式化
    return text
        .replace(/\n/g, '<br>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>');
}

// 流式聊天
async function streamChat(message, messageElement) {
    const contentDiv = messageElement.querySelector('.message-content');
    const messagesDiv = document.getElementById('messages');

    // 创建工作流展示区域
    const workflowDiv = document.createElement('div');
    workflowDiv.className = 'workflow';
    workflowDiv.innerHTML = `
        <div class="workflow-header" onclick="toggleWorkflow(this)">
            <span>🔍 Agent 工作流程</span>
            <span>▼</span>
        </div>
        <div class="workflow-content"></div>
    `;
    contentDiv.innerHTML = '';
    contentDiv.appendChild(workflowDiv);

    const workflowContent = workflowDiv.querySelector('.workflow-content');

    // 响应文本容器
    const responseDiv = document.createElement('div');
    responseDiv.style.marginTop = '10px';
    contentDiv.appendChild(responseDiv);

    let responseText = '';

    try {
        // 发送请求
        const response = await fetch(`${CONFIG.apiUrl}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': idToken
            },
            body: JSON.stringify({
                message: message,
                user_id: 'current_user'
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        // 检查是否支持流式响应
        if (response.headers.get('content-type')?.includes('text/event-stream')) {
            // SSE 流式响应
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // 保留不完整的行

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data.trim()) {
                            try {
                                const event = JSON.parse(data);
                                handleStreamEvent(event, workflowContent, responseDiv, (text) => {
                                    responseText += text;  // accumulate tokens
                                    responseDiv.innerHTML = formatMessage(responseText);
                                });
                            } catch (e) {
                                console.error('Parse error:', e);
                            }
                        }
                    }
                }

                // 自动滚动
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }
        } else {
            // 非流式响应（降级）
            const data = await response.json();
            responseText = data.content || data.message || '收到响应';
            responseDiv.innerHTML = formatMessage(responseText);
        }

        // 如果没有响应文本，显示提示
        if (!responseText) {
            responseDiv.innerHTML = '<p style="color: gray;">Agent 完成处理</p>';
        }

    } catch (error) {
        console.error('Stream error:', error);
        responseDiv.innerHTML = `<p style="color: red;">连接错误: ${error.message}</p>`;
    }

    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// 处理流式事件
function handleStreamEvent(event, workflowDiv, responseDiv, updateResponse) {
    const type = event.type;
    const content = event.content;

    switch (type) {
        case 'thinking':
            const thinkingItem = document.createElement('div');
            thinkingItem.className = 'workflow-item thinking';
            thinkingItem.textContent = `💭 思考: ${content}`;
            workflowDiv.appendChild(thinkingItem);
            break;

        case 'tool_call':
            const toolCallItem = document.createElement('div');
            toolCallItem.className = 'workflow-item tool-call';
            const toolName = content.tool || '未知工具';
            const toolInput = JSON.stringify(content.input || {}, null, 2);
            toolCallItem.innerHTML = `🔧 调用工具: <strong>${toolName}</strong><br><pre>${toolInput}</pre>`;
            workflowDiv.appendChild(toolCallItem);
            break;

        case 'tool_result':
            const toolResultItem = document.createElement('div');
            toolResultItem.className = 'workflow-item tool-result';
            toolResultItem.innerHTML = `✅ 工具结果:<br><pre style="white-space: pre-wrap; word-break: break-word;">${typeof content === 'string' ? content : JSON.stringify(content, null, 2)}</pre>`;
            workflowDiv.appendChild(toolResultItem);
            break;

        case 'text':
            updateResponse(content);  // accumulate token
            break;

        case 'done':
            // 完成
            break;

        case 'error':
            responseDiv.innerHTML = `<p style="color: red;">错误: ${content}</p>`;
            break;

        default:
            console.log('Unknown event type:', type, content);
    }
}

// 切换工作流显示
function toggleWorkflow(header) {
    const content = header.nextElementSibling;
    const arrow = header.querySelector('span:last-child');

    if (content.classList.contains('collapsed')) {
        content.classList.remove('collapsed');
        arrow.textContent = '▼';
    } else {
        content.classList.add('collapsed');
        arrow.textContent = '▶';
    }
}

// 页面加载时检查认证状态
window.addEventListener('DOMContentLoaded', () => {
    checkAuth();
});
