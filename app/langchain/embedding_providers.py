"""
Embedding 提供商管理模块

读取 .env 中 EMBED_* 字段（角色化配置）。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from loguru import logger

from app.langchain.config import ConfigError, langchain_config, load_role


class EmbeddingProvider(str, Enum):
    """保留枚举，兼容旧代码导入"""
    DASHSCOPE = "dashscope"
    OLLAMA = "ollama"


class BaseEmbeddingProvider(ABC):
    """Embedding 提供商基类"""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    def dimension_detected(self) -> bool:
        """维度是否已确定"""
        return True

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]: ...

    async def embed_query(self, text: str) -> List[float]:
        results = await self.embed_texts([text])
        return results[0]

    async def detect_dimension(self) -> int:
        """探测实际维度（子类可覆盖）"""
        return self.dimension

    def get_metadata(self) -> dict:
        return {
            "embedding_provider": self.provider_name,
            "embedding_model": self.model_name,
            "embedding_dimension": self.dimension,
        }


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """
    通用 OpenAI 兼容 Embedding 提供商。

    Ollama 和 DashScope 均支持 /v1/embeddings 接口，无需分开实现。
    """

    def __init__(
        self,
            base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimension: Optional[int] = None,
            role: str = "EMBED",
    ):
        from langchain_openai import OpenAIEmbeddings

        cfg = load_role(role)
        self._role = role
        self._base_url = base_url or cfg.base_url
        self._api_key = api_key or cfg.api_key
        self._model = model or cfg.model
        self._configured_dimension = dimension if dimension is not None else cfg.dimension
        self._batch_size = cfg.batch_size if cfg.batch_size is not None else 10
        self._actual_dimension: Optional[int] = None

        self._embeddings = OpenAIEmbeddings(
            model=self._model,
            base_url=self._base_url,
            api_key=self._api_key,
            # 显式传 dimensions：DashScope text-embedding-v4 默认 1024，
            # 需要显式指定才能输出 2048 等非默认维度。
            # 不支持 dimensions 参数的模型（如旧版 Ollama）会忽略此字段。
            **({"dimensions": self._configured_dimension} if self._configured_dimension else {}),
            # langchain-openai>=1.x 默认会用 tiktoken 把文本切成 token id 再发请求，
            # OpenAI 官方支持，但 DashScope/Ollama 等兼容接口只接受字符串，会报
            # "contents is neither str nor list of str"。这里强制走原始字符串路径。
            check_embedding_ctx_length=False,
        )

    @property
    def provider_name(self) -> str:
        if self._role and self._role != "EMBED":
            # 自定义角色直接返回 role 小写做区分（如 LOCAL_EMBED → local_embed）
            return self._role.lower()
        if "dashscope" in (self._base_url or ""):
            return "dashscope"
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        if self._configured_dimension is not None:
            return self._configured_dimension
        if self._actual_dimension is not None:
            return self._actual_dimension
        raise ValueError(
            "Embedding 维度未确定。请设置 EMBED_DIMENSION 环境变量，"
            "或先调用 detect_dimension() 自动探测。"
        )

    @property
    def dimension_detected(self) -> bool:
        return self._configured_dimension is not None or self._actual_dimension is not None

    async def detect_dimension(self) -> int:
        if self._configured_dimension is not None:
            self._actual_dimension = self._configured_dimension
            return self._configured_dimension
        if self._actual_dimension is not None:
            return self._actual_dimension
        logger.info(f"探测 Embedding 模型 {self._model} 的向量维度...")
        test_embedding = await self._embeddings.aembed_query("dimension detection")
        self._actual_dimension = len(test_embedding)
        logger.info(f"Embedding 模型 {self._model} 实际维度: {self._actual_dimension}")
        return self._actual_dimension

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # 按 EMBED_BATCH_SIZE 分块；DashScope 兼容接口对 v3/v4 限制每批 10 条。
        # 多块之间并发执行，并发上限由 EMBEDDING_MAX_CONCURRENT 控制（默认 8,避免 DashScope 限流）。
        import asyncio
        batch_size = max(1, self._batch_size)
        max_concurrent = max(1, langchain_config.EMBEDDING_MAX_CONCURRENT)
        sem = asyncio.Semaphore(max_concurrent)

        chunks = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

        async def _embed_chunk(chunk: List[str]) -> List[List[float]]:
            async with sem:
                return await self._embeddings.aembed_documents(chunk)

        try:
            parts = await asyncio.gather(*(_embed_chunk(c) for c in chunks))
            all_embeddings: List[List[float]] = [emb for part in parts for emb in part]
            if self._configured_dimension is not None and all_embeddings:
                all_embeddings = [emb[:self._configured_dimension] for emb in all_embeddings]
            if self._actual_dimension is None and all_embeddings:
                self._actual_dimension = len(all_embeddings[0])
            await self._record_billing(texts)
            return all_embeddings
        except Exception as e:
            logger.error(f"Embedding 调用出错: {e}")
            raise

    async def embed_query(self, text: str) -> List[float]:
        try:
            embedding = await self._embeddings.aembed_query(text)
            if self._configured_dimension is not None:
                embedding = embedding[:self._configured_dimension]
            if self._actual_dimension is None:
                self._actual_dimension = len(embedding)
            await self._record_billing([text])
            return embedding
        except Exception as e:
            logger.error(f"Embedding 查询出错: {e}")
            raise

    async def _record_billing(self, texts: List[str]) -> None:
        """估算 token 用量并落账。

        DashScope/OpenAI 的 /v1/embeddings 接口在 langchain-openai 包装后 usage 字段不暴露，
        这里用 len(text)/4 估算（中英混合的常用经验值）；偏差可在改单价时一次性校正。
        """
        try:
            from app.langchain.billing.pricing import Billing

            tokens = sum(max(1, len(t) // 4) for t in texts if t)
            if not tokens:
                return
            await Billing.record(
                module="embed",
                provider=self.provider_name,
                model=self._model,
                units={"token_in": tokens},
            )
        except Exception:
            logger.exception("[Billing] embedding 计费失败（已忽略）")


_instance: Optional[OpenAICompatibleEmbeddingProvider] = None
_local_instance: Optional[OpenAICompatibleEmbeddingProvider] = None


def get_embedding(
    provider: Optional[EmbeddingProvider] = None,
    model: Optional[str] = None,
    dimension: Optional[int] = None,
        **kwargs,
) -> OpenAICompatibleEmbeddingProvider:
    """
    获取 Embedding 实例（单例）。

    切换提供商只需修改 .env：
        EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
        EMBED_API_KEY=sk-xxx
        EMBED_MODEL=text-embedding-v4
        EMBED_DIMENSION=1024
    """
    global _instance
    if _instance is None or model is not None or dimension is not None:
        inst = OpenAICompatibleEmbeddingProvider(model=model, dimension=dimension)
        if model is None and dimension is None:
            _instance = inst
        return inst
    return _instance


_VALID_EMBED_PROVIDERS = {"web-embed", "local-embed"}


def get_embed_provider() -> str:
    """读取并校验 EMBED_PROVIDER（必填，web-embed 或 local-embed）。"""
    import os
    provider = (os.getenv("EMBED_PROVIDER") or "").strip().lower()
    if provider not in _VALID_EMBED_PROVIDERS:
        raise ConfigError(
            f"EMBED_PROVIDER 未配置或值无效（当前={provider!r}）。"
            f"必须设为 {_VALID_EMBED_PROVIDERS} 之一。"
        )
    return provider


def vec_table_suffix() -> str:
    """返回当前 EMBED_PROVIDER + 维度 对应的向量表名后缀，如 '_web_2048' / '_local_4096'。

    维度编入表名：切换维度时旧表保留，切回来直接用，无需重建。
    """
    import os
    provider = get_embed_provider()
    if provider == "web-embed":
        dim = int(os.getenv("EMBED_DIMENSION", "1024") or 1024)
    else:
        dim = int(
            os.getenv("LOCAL_EMBED_DIMENSION")
            or os.getenv("EMBED_DIMENSION", "1024")
            or 1024
        )
    return f"_{provider.split('-')[0]}_{dim}"


def get_local_embedding() -> OpenAICompatibleEmbeddingProvider:
    """
    向量库 Embedding 实例（单例）。

    通过 EMBED_PROVIDER（必填）控制走哪套角色配置：
      - web-embed   → 复用 EMBED_* 角色块（云端 embedding 服务，如百炼）
      - local-embed → 使用独立的 LOCAL_EMBED_* 角色块（本地部署）

    用于标准向量库（standard_vec_*）的 builder 嵌入与 qa_agent 的语义检索工具——
    存与查必须使用同一向量空间，因此整套向量库都走这个 provider。
    """
    global _local_instance
    if _local_instance is None:
        provider = get_embed_provider()
        role = "EMBED" if provider == "web-embed" else "LOCAL_EMBED"
        _local_instance = OpenAICompatibleEmbeddingProvider(role=role)
    return _local_instance
