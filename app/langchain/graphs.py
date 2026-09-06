"""
LangGraph 工作流模块

使用 LangGraph 定义复杂的 AI 工作流
"""

from typing import Annotated, TypedDict, Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from app.langchain.llm_providers import get_llm


class ChatState(TypedDict):
    """聊天状态定义"""
    messages: Annotated[list, add_messages]
    user_info: Optional[dict]


def create_simple_chat_graph(
    model: Optional[str] = None,
) -> StateGraph:
    """
    创建简单的对话图工作流

    这是一个基础示例,展示如何使用 LangGraph 构建对话流程

    Args:
        model: 模型名称

    Returns:
        编译后的图对象

    Examples:
        >>> graph = create_simple_chat_graph()
        >>> result = graph.invoke({
        ...     "messages": [HumanMessage(content="你好")]
        ... })
        >>> print(result["messages"][-1].content)
    """
    llm = get_llm(model=model)

    async def chat_node(state: ChatState):
        """对话节点（异步）"""
        response = await llm.ainvoke(state["messages"])
        return {"messages": [response]}

    # 构建图
    workflow = StateGraph(ChatState)

    # 添加节点
    workflow.add_node("chat", chat_node)

    # 设置入口点
    workflow.set_entry_point("chat")

    # 添加边
    workflow.add_edge("chat", END)

    # 编译图
    return workflow.compile()


class MultiStepTaskState(TypedDict):
    """多步骤任务状态"""
    messages: Annotated[list, add_messages]
    task_description: str
    steps: list[str]
    current_step: int
    results: list[str]
    final_answer: Optional[str]


def create_multi_step_workflow(
    model: Optional[str] = None,
) -> StateGraph:
    """
    创建多步骤任务处理工作流

    这个工作流演示如何将复杂任务分解为多个步骤并依次执行

    Args:
        model: 模型名称

    Returns:
        编译后的图对象

    工作流步骤:
        1. plan: 分析任务并制定执行计划
        2. execute_step: 执行当前步骤
        3. review: 检查是否所有步骤都已完成
        4. synthesize: 综合所有步骤的结果生成最终答案

    Examples:
        >>> graph = create_multi_step_workflow()
        >>> result = graph.invoke({
        ...     "messages": [],
        ...     "task_description": "研究 Python 的异步编程",
        ...     "steps": [],
        ...     "current_step": 0,
        ...     "results": [],
        ...     "final_answer": None
        ... })
    """
    llm = get_llm(model=model)

    async def plan_node(state: MultiStepTaskState):
        """规划节点:分解任务为多个步骤（异步）"""
        task = state["task_description"]

        planning_prompt = f"""
请将以下任务分解为 3-5 个具体的执行步骤:

任务: {task}

要求:
- 每个步骤要清晰、可执行
- 步骤之间要有逻辑顺序
- 用列表形式输出,每行一个步骤

步骤:"""

        response = await llm.ainvoke([HumanMessage(content=planning_prompt)])
        steps_text = response.content

        # 解析步骤(简单按行分割)
        steps = [
            line.strip().lstrip("1234567890.-) ")
            for line in steps_text.split("\n")
            if line.strip() and any(c.isalpha() for c in line)
        ]

        return {
            "steps": steps,
            "current_step": 0,
            "messages": [HumanMessage(content=planning_prompt), response]
        }

    async def execute_step_node(state: MultiStepTaskState):
        """执行节点:执行当前步骤（异步）"""
        current_step_idx = state["current_step"]
        steps = state["steps"]

        if current_step_idx >= len(steps):
            return state

        current_step = steps[current_step_idx]

        execution_prompt = f"""
请执行以下步骤:

步骤 {current_step_idx + 1}/{len(steps)}: {current_step}

任务背景: {state['task_description']}

请提供该步骤的详细执行结果:"""

        response = await llm.ainvoke([HumanMessage(content=execution_prompt)])

        results = state["results"].copy()
        results.append(response.content)

        return {
            "current_step": current_step_idx + 1,
            "results": results,
            "messages": state["messages"] + [
                HumanMessage(content=execution_prompt),
                response
            ]
        }

    def should_continue(state: MultiStepTaskState):
        """决定是否继续执行步骤"""
        if state["current_step"] < len(state["steps"]):
            return "execute_step"
        else:
            return "synthesize"

    async def synthesize_node(state: MultiStepTaskState):
        """综合节点:整合所有步骤的结果（异步）"""
        task = state["task_description"]
        steps = state["steps"]
        results = state["results"]

        synthesis_prompt = f"""
任务: {task}

执行的步骤和结果:

{chr(10).join(f"{i+1}. 步骤: {step}{chr(10)}   结果: {result}{chr(10)}" for i, (step, result) in enumerate(zip(steps, results)))}

请综合以上所有步骤的结果,给出对原始任务的完整答案:"""

        response = await llm.ainvoke([HumanMessage(content=synthesis_prompt)])

        return {
            "final_answer": response.content,
            "messages": state["messages"] + [
                HumanMessage(content=synthesis_prompt),
                response
            ]
        }

    # 构建图
    workflow = StateGraph(MultiStepTaskState)

    # 添加节点
    workflow.add_node("plan", plan_node)
    workflow.add_node("execute_step", execute_step_node)
    workflow.add_node("synthesize", synthesize_node)

    # 设置入口点
    workflow.set_entry_point("plan")

    # 添加边
    workflow.add_edge("plan", "execute_step")
    workflow.add_conditional_edges(
        "execute_step",
        should_continue,
        {
            "execute_step": "execute_step",
            "synthesize": "synthesize"
        }
    )
    workflow.add_edge("synthesize", END)

    # 编译图
    return workflow.compile()


class ReflectionState(TypedDict):
    """反思工作流状态"""
    messages: Annotated[list, add_messages]
    draft: Optional[str]
    critique: Optional[str]
    final_response: Optional[str]
    iteration: int
    max_iterations: int


def create_reflection_workflow(
    model: Optional[str] = None,
    max_iterations: int = 2,
) -> StateGraph:
    """
    创建带反思机制的工作流

    这个工作流演示 AI 自我反思和改进的过程:
    1. 生成初稿
    2. 自我批评和评估
    3. 根据批评改进
    4. 重复直到满意或达到最大迭代次数

    Args:
        model: 模型名称
        max_iterations: 最大迭代次数

    Returns:
        编译后的图对象

    Examples:
        >>> graph = create_reflection_workflow()
        >>> result = graph.invoke({
        ...     "messages": [HumanMessage(content="写一篇关于 AI 的短文")],
        ...     "draft": None,
        ...     "critique": None,
        ...     "final_response": None,
        ...     "iteration": 0,
        ...     "max_iterations": 2
        ... })
    """
    llm = get_llm(model=model)

    async def generate_node(state: ReflectionState):
        """生成节点:生成或改进内容（异步）"""
        if state["iteration"] == 0:
            # 首次生成
            prompt = "请回答以下问题:\n\n" + state["messages"][-1].content
        else:
            # 根据批评改进
            prompt = f"""
之前的回答:
{state['draft']}

改进建议:
{state['critique']}

请根据以上建议,生成改进后的回答:"""

        response = await llm.ainvoke([HumanMessage(content=prompt)])

        return {
            "draft": response.content,
            "messages": state["messages"] + [
                HumanMessage(content=prompt),
                response
            ]
        }

    async def critique_node(state: ReflectionState):
        """批评节点:评估当前生成的内容（异步）"""
        critique_prompt = f"""
请评估以下回答的质量:

{state['draft']}

评估维度:
1. 准确性:信息是否准确
2. 完整性:是否全面回答了问题
3. 清晰度:表达是否清晰易懂
4. 改进建议:如何改进

请提供具体的改进建议:"""

        response = await llm.ainvoke([HumanMessage(content=critique_prompt)])

        return {
            "critique": response.content,
            "iteration": state["iteration"] + 1,
            "messages": state["messages"] + [
                HumanMessage(content=critique_prompt),
                response
            ]
        }

    def should_continue(state: ReflectionState):
        """决定是否继续迭代"""
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        else:
            return "generate"

    def finalize_node(state: ReflectionState):
        """最终化节点:确定最终答案"""
        return {
            "final_response": state["draft"]
        }

    # 构建图
    workflow = StateGraph(ReflectionState)

    # 添加节点
    workflow.add_node("generate", generate_node)
    workflow.add_node("critique", critique_node)
    workflow.add_node("finalize", finalize_node)

    # 设置入口点
    workflow.set_entry_point("generate")

    # 添加边
    workflow.add_edge("generate", "critique")
    workflow.add_conditional_edges(
        "critique",
        should_continue,
        {
            "generate": "generate",
            "finalize": "finalize"
        }
    )
    workflow.add_edge("finalize", END)

    # 编译图
    graph = workflow.compile()

    # 设置默认的 max_iterations
    def invoke_with_defaults(input_data):
        if "max_iterations" not in input_data:
            input_data["max_iterations"] = max_iterations
        return graph.invoke(input_data)

    graph.invoke = invoke_with_defaults

    return graph
