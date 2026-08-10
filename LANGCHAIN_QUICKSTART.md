# LangChain 集成快速开始

欢迎使用 CESI Fast Admin 的 LangChain 集成!本指南将帮助你快速上手。

## 🚀 快速开始

### 1. 配置环境变量

复制环境变量模板:
```bash
cp .env.langchain.example .env
```

编辑 `.env` 文件,选择以下任一配置:

**选项 A: 使用 Ollama (推荐用于开发和隐私保护)**
```env
DEFAULT_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

**选项 B: 使用通义千问**
```env
DEFAULT_LLM_PROVIDER=qwen
QWEN_API_KEY=your-api-key-here
QWEN_MODEL=qwen-turbo
```

### 2. 安装 Ollama (如果选择 Ollama)

访问 https://ollama.ai 下载安装 Ollama

然后拉取模型:
```bash
ollama pull llama3.2
```

Ollama 会自动在后台运行。

### 3. 运行测试

验证集成是否成功:
```bash
python tests/test_langchain_integration.py
```

### 4. 运行示例

查看各种使用示例:
```bash
python examples/langchain_examples.py
```

### 5. 启动应用

```bash
python run.py
```

访问 API 文档: http://localhost:9999/docs

## 📚 功能特性

### ✅ 已集成的功能

1. **LangChain 链**
   - 简单对话链
   - 代码助手链
   - 文档问答链
   - 流式对话支持

2. **LangGraph 工作流**
   - 简单对话图
   - 多步骤任务工作流
   - 反思改进工作流

3. **LangServe API**
   - 标准化的 API 端点
   - invoke/batch/stream 操作
   - 交互式 Playground

4. **LLM 提供商**
   - Ollama (本地模型)
   - 通义千问 (在线 API)
   - 易于扩展更多提供商

## 🎯 API 端点

### 对话接口

- `POST /api/v1/ai/chat/simple` - 简单对话
- `POST /api/v1/ai/chat/code-assistant` - 代码助手
- `POST /api/v1/ai/chat/stream` - 流式对话
- `POST /api/v1/ai/chat/document-qa` - 文档问答

### 工作流接口

- `POST /api/v1/ai/workflows/simple-chat` - 简单对话图
- `POST /api/v1/ai/workflows/multi-step` - 多步骤任务
- `POST /api/v1/ai/workflows/reflection` - 反思改进
- `GET /api/v1/ai/workflows/available` - 获取可用工作流

### LangServe 接口

- `/api/v1/ai/langserve/simple-chat/invoke` - 调用简单对话链
- `/api/v1/ai/langserve/simple-chat/stream` - 流式调用
- `/api/v1/ai/langserve/simple-chat/batch` - 批量调用
- `/api/v1/ai/langserve/simple-chat/playground` - 交互式界面

更多端点请访问 API 文档。

## 📖 使用示例

### cURL 示例

```bash
# 简单对话
curl -X POST http://localhost:9999/api/v1/ai/chat/simple \
  -H "Content-Type: application/json" \
  -d '{"message": "你好,介绍一下你自己"}'

# 多步骤任务
curl -X POST http://localhost:9999/api/v1/ai/workflows/multi-step \
  -H "Content-Type: application/json" \
  -d '{"task": "研究 Python 的装饰器"}'
```

### Python 示例

```python
import requests

# 简单对话
response = requests.post(
    "http://localhost:9999/api/v1/ai/chat/simple",
    json={"message": "你好"}
)
print(response.json())

# 代码助手
response = requests.post(
    "http://localhost:9999/api/v1/ai/chat/code-assistant",
    json={"message": "如何用 Python 读取 JSON 文件?"}
)
print(response.json())
```

### JavaScript 示例

```javascript
// 简单对话
const response = await fetch('http://localhost:9999/api/v1/ai/chat/simple', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({message: '你好'})
});
const data = await response.json();
console.log(data);
```

## 🏗️ 项目结构

```
app/
├── langchain/                  # LangChain 集成模块
│   ├── config.py              # 配置管理
│   ├── llm_providers.py       # LLM 提供商
│   ├── prompts.py             # 提示词模板
│   ├── chains.py              # LangChain 链
│   └── graphs.py              # LangGraph 工作流
│
└── api/v1/ai/                 # AI API 路由
    ├── chat.py                # 对话接口
    ├── workflows.py           # 工作流接口
    └── langserve_routes.py    # LangServe 路由

docs/
└── LANGCHAIN_INTEGRATION.md   # 详细文档

tests/
└── test_langchain_integration.py  # 集成测试

examples/
└── langchain_examples.py      # 使用示例
```

## 💡 核心概念

### LangChain 链

链是 LangChain 的基本构建块,将提示词、LLM 和输出解析器组合在一起:

```
Prompt → LLM → Output Parser → Result
```

### LangGraph 工作流

LangGraph 用于构建复杂的、有状态的工作流:

```
State → Node 1 → Node 2 → ... → Final State
```

### LangServe

LangServe 将 LangChain 链和图暴露为标准的 REST API:

```
Chain/Graph → LangServe → REST API
```

## 🔧 配置选项

### 温度参数

控制输出的随机性 (0.0 - 1.0):
- **0.0-0.3**: 确定性输出,适合事实性任务
- **0.5-0.7**: 平衡,适合一般对话
- **0.7-1.0**: 创意输出,适合创意写作

### 模型选择

**Ollama 模型**:
- `llama3.2`: Meta 最新模型,通用性强
- `qwen2`: 阿里千问 2,中文能力强
- `mistral`: Mistral AI,代码能力强

**通义千问模型**:
- `qwen-turbo`: 快速响应
- `qwen-plus`: 能力更强
- `qwen-max`: 最强模型

## 🐛 常见问题

### Q: Ollama 连接失败?
**A**: 确保 Ollama 已安装并运行:
```bash
ollama --version
ollama list
```

### Q: 响应速度慢?
**A**:
- 使用更小的模型 (如 `llama3.2:1b`)
- 使用流式接口获得更好的用户体验
- 考虑使用 GPU 加速

### Q: 如何添加对话历史?
**A**: 在请求中包含 `chat_history` 字段:
```json
{
  "message": "继续",
  "chat_history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好!"}
  ]
}
```

## 📚 学习资源

- [详细集成文档](docs/LANGCHAIN_INTEGRATION.md)
- [API 文档](http://localhost:9999/docs) (启动应用后访问)
- [LangChain 官方文档](https://python.langchain.com/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [Ollama 官网](https://ollama.ai/)

## 🎉 下一步

1. ✅ 运行测试验证集成
2. ✅ 尝试各种示例
3. ✅ 查看 API 文档
4. ✅ 根据需求定制和扩展

## 🤝 贡献

欢迎提交 Issue 和 Pull Request!

## 📄 许可证

MIT License

---

**开始探索 AI 的强大能力吧!** 🚀
