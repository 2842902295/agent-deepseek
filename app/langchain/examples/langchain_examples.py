"""
LangChain 使用示例

展示如何使用集成的 LangChain 功能
"""

import asyncio
from langchain_core.messages import HumanMessage


async def example_simple_chat():
    """示例 1: 简单对话"""
    print("\n" + "=" * 60)
    print("示例 1: 简单对话")
    print("=" * 60)

    from app.langchain.chains import ChainFactory

    chain = ChainFactory.create_simple_chat_chain()

    result = await chain.ainvoke({
        "input": "你好!请简单介绍一下你自己。"
    })

    print(f"\n用户: 你好!请简单介绍一下你自己。")
    print(f"AI: {result}")


async def example_code_assistant():
    """示例 2: 代码助手"""
    print("\n" + "=" * 60)
    print("示例 2: 代码助手")
    print("=" * 60)

    from app.langchain.chains import ChainFactory

    chain = ChainFactory.create_code_assistant_chain()

    result = await chain.ainvoke({
        "input": "如何用 Python 异步读取多个文件?"
    })

    print(f"\n用户: 如何用 Python 异步读取多个文件?")
    print(f"AI:\n{result}")


async def example_streaming_chat():
    """示例 3: 流式对话"""
    print("\n" + "=" * 60)
    print("示例 3: 流式对话")
    print("=" * 60)

    from app.langchain.chains import create_streaming_chain

    chain = create_streaming_chain()

    print(f"\n用户: 讲一个关于程序员的笑话")
    print(f"AI: ", end="", flush=True)

    async for chunk in chain.astream({
        "input": "讲一个关于程序员的笑话"
    }):
        print(chunk, end="", flush=True)

    print()  # 换行


async def example_document_qa():
    """示例 4: 文档问答"""
    print("\n" + "=" * 60)
    print("示例 4: 文档问答")
    print("=" * 60)

    from app.langchain.chains import ChainFactory

    context = """
    FastAPI 是一个现代、快速(高性能)的 web 框架,用于构建 APIs。
    它基于 Python 3.6+ 的类型提示。

    主要特性:
    - 快速:非常高的性能,可与 NodeJS 和 Go 相媲美
    - 高效编码:提高功能开发速度约 200% 至 300%
    - 更少 bug:减少约 40% 的人为错误
    - 直观:极佳的编辑器支持,自动补全
    - 简单:设计易于使用和学习
    - 简短:最小化代码重复
    """

    chain = ChainFactory.create_document_qa_chain(context=context)

    questions = [
        "FastAPI 的主要优势是什么?",
        "FastAPI 是否支持异步?",
    ]

    for question in questions:
        result = await chain.ainvoke({"input": question})
        print(f"\n问题: {question}")
        print(f"回答: {result}")


async def example_simple_graph():
    """示例 5: 简单对话图"""
    print("\n" + "=" * 60)
    print("示例 5: LangGraph 简单对话图")
    print("=" * 60)

    from app.langchain.graphs import create_simple_chat_graph

    graph = create_simple_chat_graph()

    result = await graph.ainvoke({
        "messages": [HumanMessage(content="什么是 LangGraph?为什么要使用它?")]
    })

    print(f"\n用户: 什么是 LangGraph?为什么要使用它?")
    print(f"AI: {result['messages'][-1].content}")


async def example_multi_step():
    """示例 6: 多步骤工作流"""
    print("\n" + "=" * 60)
    print("示例 6: 多步骤任务工作流")
    print("=" * 60)

    from app.langchain.graphs import create_multi_step_workflow

    graph = create_multi_step_workflow()

    task = "研究 Docker 容器技术"

    print(f"\n任务: {task}")
    print("\n正在执行...")

    result = await graph.ainvoke({
        "messages": [],
        "task_description": task,
        "steps": [],
        "current_step": 0,
        "results": [],
        "final_answer": None
    })

    print(f"\n执行步骤:")
    for i, step in enumerate(result["steps"], 1):
        print(f"{i}. {step}")

    print(f"\n最终答案:\n{result['final_answer']}")


async def example_reflection():
    """示例 7: 反思工作流"""
    print("\n" + "=" * 60)
    print("示例 7: 反思改进工作流")
    print("=" * 60)

    from app.langchain.graphs import create_reflection_workflow

    graph = create_reflection_workflow(max_iterations=2)

    question = "解释什么是微服务架构"

    print(f"\n问题: {question}")
    print("\n正在执行...")

    result = await graph.ainvoke({
        "messages": [HumanMessage(content=question)],
        "draft": None,
        "critique": None,
        "final_response": None,
        "iteration": 0,
        "max_iterations": 2
    })

    print(f"\n迭代次数: {result['iteration']}")
    print(f"\n最终回答:\n{result['final_response']}")


async def example_with_different_providers():
    """示例 8: 使用不同的 LLM 提供商"""
    print("\n" + "=" * 60)
    print("示例 8: 使用不同的 LLM 提供商")
    print("=" * 60)

    from app.langchain.llm_providers import get_llm, LLMProvider

    question = "什么是人工智能?"

    # 使用 Ollama
    try:
        print("\n使用 Ollama:")
        llm_ollama = get_llm(provider=LLMProvider.OLLAMA)
        result = await llm_ollama.ainvoke(question)
        print(f"回答: {result.content[:200]}...")
    except Exception as e:
        print(f"Ollama 不可用: {e}")

    # 使用 CHAT 角色（不再区分具体 provider）
    try:
        print("\n使用 CHAT 角色:")
        llm_chat = get_llm()
        result = await llm_chat.ainvoke(question)
        print(f"回答: {result.content[:200]}...")
    except Exception as e:
        print(f"CHAT 不可用: {e}")


async def example_with_temperature():
    """示例 9: 不同温度参数的效果"""
    print("\n" + "=" * 60)
    print("示例 9: 不同温度参数的效果")
    print("=" * 60)

    from app.langchain.chains import ChainFactory

    question = "用一句话描述春天"

    temperatures = [0.1, 0.5, 0.9]

    for temp in temperatures:
        chain = ChainFactory.create_simple_chat_chain(temperature=temp)
        result = await chain.ainvoke({"input": question})
        print(f"\n温度 {temp}: {result}")


async def main():
    """运行所有示例"""
    print("=" * 60)
    print("LangChain 集成使用示例")
    print("=" * 60)
    print("\n提示: 这些示例需要 Ollama 或通义千问运行")
    print("按 Ctrl+C 可以随时中断\n")

    examples = [
        ("简单对话", example_simple_chat),
        ("代码助手", example_code_assistant),
        ("流式对话", example_streaming_chat),
        ("文档问答", example_document_qa),
        ("简单对话图", example_simple_graph),
        ("多步骤工作流", example_multi_step),
        ("反思工作流", example_reflection),
        ("不同提供商", example_with_different_providers),
        ("温度参数", example_with_temperature),
    ]

    for name, example_func in examples:
        try:
            await example_func()
            await asyncio.sleep(1)  # 稍微等待一下
        except KeyboardInterrupt:
            print("\n\n用户中断")
            break
        except Exception as e:
            print(f"\n示例 '{name}' 执行失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("示例运行完毕")
    print("=" * 60)
    print("\n查看详细文档: docs/LANGCHAIN_INTEGRATION.md")


if __name__ == "__main__":
    asyncio.run(main())
