"""
Embedding 提供商管理模块

统一走 EMBED 角色（经 config.load_role 的激活块重定向，指向超管在模型配置页
选中的 embed 预设块，默认 EMBED_DASHSCOPE；块定义物化见 model_selection.py）。
全体系（统一向量库 / seekdb 知识库 / 比对）共用这一个入口，同一个向量空间。

限流保护：所有嵌入请求带指数退避重试（429 / 5xx / 超时才重试，其余错误透传），
配合 EMBEDDING_MAX_CONCURRENT 并发上限，防止大批量构建打爆厂商配额
（2026-08-26 FAISS 全量重建 429 事故后加固）。
"""

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, List, Optional

from loguru import logger

from app.langchain.config import langchain_config, load_role

# ── 限流重试（embed_texts / embed_query 共用）────────────────────────────────
# 仅对限流/瞬时故障重试：429、5xx、超时、连接错误；其余（鉴权失败、参数错误等）
# 立即透传，不吞错。退避：2s × 2^n + 抖动，封顶 30s。
_EMBED_RETRY_MAX = 5


def _is_retryable_embed_error(e: Exception) -> bool:
    """判断嵌入请求异常是否值得重试（不硬依赖 openai SDK 类型）。"""
    status = getattr(e, "status_code", None)
    if status == 429 or (isinstance(status, int) and 500 <= status < 600):
        return True
    return type(e).__name__ in (
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectionError",
    )


async def _retry_embed_call(factory: Callable[[], Awaitable]):
    """执行一次嵌入调用，限流/瞬时故障按指数退避重试。"""
    for attempt in range(_EMBED_RETRY_MAX):
        try:
            return await factory()
        except Exception as e:
            if attempt >= _EMBED_RETRY_MAX - 1 or not _is_retryable_embed_error(e):
                raise
            delay = min(2.0 * (2**attempt), 30.0) + random.uniform(0.0, 1.0)
            logger.warning(f"[embedding] 第 {attempt + 1} 次请求失败（{type(e).__name__}: {e}），{delay:.1f}s 后重试")
            await asyncio.sleep(delay)


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
                return await _retry_embed_call(lambda: self._embeddings.aembed_documents(chunk))

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
            embedding = await _retry_embed_call(lambda: self._embeddings.aembed_query(text))
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
_instance_block: Optional[str] = None  # _instance 构建时的激活 embed 块名（块变即重建）


def get_embedding(
    model: Optional[str] = None,
    dimension: Optional[int] = None,
) -> OpenAICompatibleEmbeddingProvider:
    """
    获取 Embedding 实例（单例，跟随激活的 embed 预设块）。

    配置走 DB 模型块体系（agent_model_block，category=embed）：
    load_role("EMBED") 经激活块重定向读取选中块的物化 env，超管在模型配置页
    切换后由 clear_model_caches 清空本单例（这里也按块名做重建兜底）。
    """
    global _instance, _instance_block
    from app.langchain.config import get_active_block

    block = get_active_block("embed", "EMBED_DASHSCOPE")
    if _instance is None or _instance_block != block or model is not None or dimension is not None:
        inst = OpenAICompatibleEmbeddingProvider(model=model, dimension=dimension)
        if model is None and dimension is None:
            _instance = inst
            _instance_block = block
        return inst
    return _instance
