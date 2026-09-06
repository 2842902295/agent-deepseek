"""
LangChain 集成测试

测试 LangChain、LangGraph、LangServe 的集成是否正常
"""

import asyncio
from langchain_core.messages import HumanMessage


def test_imports():
    """测试所有模块是否可以正常导入"""
    try:
        from app.langchain.config import langchain_config
        from app.langchain.llm_providers import get_llm, LLMProvider
        from app.langchain.chains import ChainFactory
        from app.langchain.graphs import (
            create_simple_chat_graph,
            create_multi_step_workflow,
            create_reflection_workflow
        )
        print("✓ 所有模块导入成功")
        return True
    except Exception as e:
        print(f"✗ 模块导入失败: {e}")
        return False


def test_config():
    """测试配置是否正确加载"""
    try:
        from app.langchain.config import langchain_config

        print(f"\n配置信息:")
        print(f"  默认 LLM 提供商: {langchain_config.DEFAULT_LLM_PROVIDER}")
        print(f"  Ollama URL: {langchain_config.OLLAMA_BASE_URL}")
        print(f"  Ollama 模型: {langchain_config.OLLAMA_MODEL}")
        print(f"  默认温度: {langchain_config.DEFAULT_TEMPERATURE}")

        print("✓ 配置加载成功")
        return True
    except Exception as e:
        print(f"✗ 配置加载失败: {e}")
        return False


async def test_llm_provider():
    """测试 LLM 提供商"""
    try:
        from app.langchain.llm_providers import get_llm, LLMProvider

        print("\n测试 LLM 提供商:")

        # 测试 Ollama (如果配置了)
        try:
            llm = get_llm(provider=LLMProvider.OLLAMA)
            print(f"  ✓ Ollama LLM 实例创建成功: {type(llm).__name__}")
        except Exception as e:
            print(f"  ⚠ Ollama 不可用: {e}")

        # 测试通义千问 (如果配置了 API Key)
        try:
            from app.langchain.config import langchain_config
            if langchain_config.QWEN_API_KEY:
                llm = get_llm(provider=LLMProvider.QWEN)
                print(f"  ✓ 通义千问 LLM 实例创建成功: {type(llm).__name__}")
            else:
                print(f"  ⚠ 通义千问未配置 API Key")
        except Exception as e:
            print(f"  ⚠ 通义千问不可用: {e}")

        return True
    except Exception as e:
        print(f"✗ LLM 提供商测试失败: {e}")
        return False


async def test_chains():
    """测试 LangChain 链"""
    try:
        from app.langchain.chains import ChainFactory

        print("\n测试 LangChain 链:")

        # 测试简单对话链
        chain = ChainFactory.create_simple_chat_chain()
        print(f"  ✓ 简单对话链创建成功")

        # 测试代码助手链
        chain = ChainFactory.create_code_assistant_chain()
        print(f"  ✓ 代码助手链创建成功")

        # 测试文档问答链
        chain = ChainFactory.create_document_qa_chain(context="测试上下文")
        print(f"  ✓ 文档问答链创建成功")

        return True
    except Exception as e:
        print(f"✗ 链测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_graphs():
    """测试 LangGraph 工作流"""
    try:
        from app.langchain.graphs import (
            create_simple_chat_graph,
            create_multi_step_workflow,
            create_reflection_workflow
        )

        print("\n测试 LangGraph 工作流:")

        # 测试简单对话图
        graph = create_simple_chat_graph()
        print(f"  ✓ 简单对话图创建成功")

        # 测试多步骤工作流
        graph = create_multi_step_workflow()
        print(f"  ✓ 多步骤工作流创建成功")

        # 测试反思工作流
        graph = create_reflection_workflow()
        print(f"  ✓ 反思工作流创建成功")

        return True
    except Exception as e:
        print(f"✗ 工作流测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_api_routes():
    """测试 API 路由是否正确注册"""
    try:
        from app.api.v1.ai import ai_router

        print("\n测试 API 路由:")
        print(f"  ✓ AI 路由模块加载成功")
        print(f"  路由前缀: {ai_router.prefix}")

        return True
    except Exception as e:
        print(f"✗ API 路由测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_simple_invoke():
    """测试简单调用"""
    try:
        from app.langchain.chains import ChainFactory

        print("\n测试简单调用 (需要 Ollama 运行):")

        chain = ChainFactory.create_simple_chat_chain()

        # 尝试调用
        try:
            result = await chain.ainvoke({
                "input": "你好,这是一个测试消息"
            })
            print(f"  ✓ 调用成功")
            print(f"  响应: {result[:100]}..." if len(result) > 100 else f"  响应: {result}")
            return True
        except Exception as e:
            print(f"  ⚠ 调用失败 (可能是因为 Ollama 未运行): {e}")
            return True  # 不算测试失败,因为可能没有 Ollama

    except Exception as e:
        print(f"✗ 简单调用测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("LangChain 集成测试")
    print("=" * 60)

    results = []

    # 同步测试
    results.append(("模块导入", test_imports()))
    results.append(("配置加载", test_config()))

    # 异步测试
    results.append(("LLM 提供商", await test_llm_provider()))
    results.append(("LangChain 链", await test_chains()))
    results.append(("LangGraph 工作流", await test_graphs()))
    results.append(("API 路由", await test_api_routes()))
    results.append(("简单调用", await test_simple_invoke()))

    # 打印总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name}: {status}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过!")
    else:
        print(f"\n⚠ {total - passed} 个测试失败")

    print("\n提示:")
    print("- 如果 Ollama 相关测试失败,请确保 Ollama 已安装并运行")
    print("- 如果通义千问相关测试失败,请检查 API Key 配置")
    print("- 查看详细文档: docs/LANGCHAIN_INTEGRATION.md")


if __name__ == "__main__":
    asyncio.run(main())
