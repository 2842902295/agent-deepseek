"""
提示词模板模块

定义各种场景的提示词模板
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


# 简单对话模板
SIMPLE_CHAT_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", "你是一个友好且专业的AI助手。请用简洁明了的方式回答用户的问题。"),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
])


# 代码助手模板
CODE_ASSISTANT_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", """你是一个专业的编程助手。

请遵循以下原则:
1. 提供清晰、可运行的代码示例
2. 解释代码的关键部分
3. 遵循最佳实践和编码规范
4. 如果问题不清楚,请要求澄清
"""),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
])


# 文档问答模板
DOCUMENT_QA_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", """基于以下上下文回答用户的问题。

上下文信息:
{context}

要求:
1. 仅基于提供的上下文回答
2. 如果上下文中没有相关信息,请明确说明
3. 回答要准确、完整
"""),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
])


# 数据分析模板
DATA_ANALYSIS_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", """你是一个数据分析专家。

可用的工具和数据:
{tools_description}

请按照以下步骤分析:
1. 理解用户的分析需求
2. 选择合适的分析方法
3. 提供清晰的分析结果和见解
4. 如果需要,建议下一步的分析方向
"""),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
])


# 工作流规划模板
WORKFLOW_PLANNING_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", """你是一个工作流规划专家。

请帮助用户:
1. 分解复杂任务为可执行的步骤
2. 确定步骤之间的依赖关系
3. 提供清晰的执行计划
4. 识别潜在的风险和挑战
"""),
    ("human", "{input}"),
])


# 获取提示词模板的字典
PROMPT_TEMPLATES = {
    "simple_chat": SIMPLE_CHAT_TEMPLATE,
    "code_assistant": CODE_ASSISTANT_TEMPLATE,
    "document_qa": DOCUMENT_QA_TEMPLATE,
    "data_analysis": DATA_ANALYSIS_TEMPLATE,
    "workflow_planning": WORKFLOW_PLANNING_TEMPLATE,
}


def get_prompt_template(template_name: str) -> ChatPromptTemplate:
    """
    根据名称获取提示词模板

    Args:
        template_name: 模板名称

    Returns:
        ChatPromptTemplate 实例

    Raises:
        ValueError: 如果模板名称不存在
    """
    if template_name not in PROMPT_TEMPLATES:
        raise ValueError(
            f"模板 '{template_name}' 不存在。"
            f"可用的模板: {', '.join(PROMPT_TEMPLATES.keys())}"
        )

    return PROMPT_TEMPLATES[template_name]
