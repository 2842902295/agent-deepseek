"""
LangChain 集成模块

提供 LangChain、LangGraph、LangServe 的集成功能
"""

from app.langchain.agents import create_sqlite_agent, run_agent_stream
from app.langchain.config import LangChainConfig
from app.langchain.embedding_providers import EmbeddingProvider, get_embedding, BaseEmbeddingProvider
from app.langchain.indicator_extractor import StandardIndicatorExtractor, Indicator, get_indicator_extractor
from app.langchain.llm_providers import get_llm, LLMProvider
from app.langchain.tools import make_db_tools

__all__ = [
    "LangChainConfig",
    "get_llm",
    "LLMProvider",
    "EmbeddingProvider",
    "get_embedding",
    "BaseEmbeddingProvider",
    "StandardIndicatorExtractor",
    "Indicator",
    "get_indicator_extractor",
    # tools
    "make_db_tools",
    # agents
    "create_sqlite_agent",
    "run_agent_stream",
]
