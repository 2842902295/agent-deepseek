"""
LangChain 链定义模块

定义各种 LangChain 链
"""

from typing import Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from app.langchain.llm_providers import get_llm
from app.langchain.prompts import get_prompt_template


class ChainFactory:
    """链工厂类,用于创建各种预定义的链"""

    @staticmethod
    def create_simple_chat_chain(
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Runnable:
        """
        创建简单对话链

        Args:
            provider: LLM 提供商
            model: 模型名称
            temperature: 温度参数

        Returns:
            可运行的链对象

        Examples:
            >>> chain = ChainFactory.create_simple_chat_chain()
            >>> result = chain.invoke({"input": "你好"})
            >>> print(result)
        """
        llm = get_llm(model=model, temperature=temperature)
        prompt = get_prompt_template("simple_chat")

        chain = prompt | llm | StrOutputParser()
        return chain

    @staticmethod
    def create_code_assistant_chain(
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Runnable:
        """
        创建代码助手链

        Args:
            provider: LLM 提供商
            model: 模型名称
            temperature: 温度参数

        Returns:
            可运行的链对象

        Examples:
            >>> chain = ChainFactory.create_code_assistant_chain()
            >>> result = chain.invoke({"input": "如何用 Python 读取 CSV 文件?"})
            >>> print(result)
        """
        llm = get_llm(model=model, temperature=temperature)
        prompt = get_prompt_template("code_assistant")

        chain = prompt | llm | StrOutputParser()
        return chain

    @staticmethod
    def create_document_qa_chain(
        context: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Runnable:
        """
        创建文档问答链

        Args:
            context: 文档上下文
            provider: LLM 提供商
            model: 模型名称
            temperature: 温度参数

        Returns:
            可运行的链对象

        Examples:
            >>> context = "Python 是一种解释型、面向对象的编程语言。"
            >>> chain = ChainFactory.create_document_qa_chain(context=context)
            >>> result = chain.invoke({"input": "Python 是什么?"})
            >>> print(result)
        """
        llm = get_llm(model=model, temperature=temperature)
        prompt = get_prompt_template("document_qa")

        chain = (
            prompt.partial(context=context)
            | llm
            | StrOutputParser()
        )
        return chain


def create_streaming_chain(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Runnable:
    """
    创建支持流式输出的简单对话链

    Args:
        provider: LLM 提供商
        model: 模型名称
        temperature: 温度参数

    Returns:
        可运行的链对象

    Examples:
        >>> chain = create_streaming_chain()
        >>> for chunk in chain.stream({"input": "讲个笑话"}):
        ...     print(chunk, end="", flush=True)
    """
    llm = get_llm(model=model, temperature=temperature)
    prompt = get_prompt_template("simple_chat")

    chain = prompt | llm | StrOutputParser()
    return chain
