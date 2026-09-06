"""
LLM 计费回调：注入到所有 LangChain Chat 实例上，自动捕获每次调用的 token 用量并落账。

实现要点：
  - 用 AsyncCallbackHandler 的 on_llm_end，DashScope/OpenAI 兼容接口走的就是这里
  - LLMResult.llm_output["token_usage"] 形如 {prompt_tokens, completion_tokens, total_tokens, ...}
    OpenAI 还会有 prompt_tokens_details.cached_tokens（缓存命中），独立计价
  - 流式调用结束时也会触发 on_llm_end，所以 stream / non-stream 都覆盖
  - role 决定 module：CHAT → "chat"，VISION → "vision"，其它角色当 chat 处理
  - serialized / kwargs 不可靠时，用闭包里的 model/base_url 兜底
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from loguru import logger

from app.langchain.billing.pricing import Billing


def _provider_from_base_url(base_url: Optional[str]) -> str:
    """从 base_url 推断 provider。未匹配时返回空串（写库时可被 LLM 回填）。"""
    if not base_url:
        return ""
    bl = base_url.lower()
    if "dashscope" in bl or "aliyuncs" in bl:
        return "dashscope"
    if "anthropic" in bl:
        return "anthropic"
    if "openai" in bl:
        return "openai"
    if "ollama" in bl or ":11434" in bl:
        return "ollama"
    return ""


def _module_from_role(role: str) -> str:
    role_u = (role or "").upper()
    if role_u == "VISION":
        return "vision"
    return "chat"


class BillingCallbackHandler(AsyncCallbackHandler):
    """LLM 用量回调：on_llm_end 时把 token 写入 agent_usage_log。

    每个 LLM 实例在 _build_chat 时创建一个独立的 handler（绑定 role/model/provider），
    所以 handler 内部可以直接持有这些信息，避免依赖 callback 触发时的 serialized 字段。
    """

    # langchain 在做 `RunnableWithCallbacks` 序列化时会走 deepcopy，handler 必须可拷贝。
    # 这里所有字段都是字符串/None，默认 dataclass 行为已经够用。

    def __init__(self, *, role: str, model: str, base_url: str) -> None:
        super().__init__()
        self._role = role
        self._module = _module_from_role(role)
        self._model = model
        self._provider = _provider_from_base_url(base_url) or "unknown"

    # 流式 + 非流式都走这里
    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            units = self._extract_units(response)
            if not units:
                return
            await Billing.record(
                module=self._module,
                provider=self._provider,
                model=self._model,
                units=units,
            )
        except Exception:
            logger.exception("[Billing] on_llm_end 处理异常（已吞掉，不影响业务）")

    @staticmethod
    def _extract_units(response: LLMResult) -> dict[str, float]:
        """从 LLMResult 抽取 token 用量。兼容三种放置位置：
           1) response.llm_output["token_usage"]    （OpenAI / DashScope 兼容接口）
           2) response.generations[*][*].message.usage_metadata  （新版 LC）
           3) response.generations[*][*].generation_info["token_usage"]  （部分实现）
        """
        token_in = 0
        token_out = 0
        token_cached = 0

        llm_output = response.llm_output or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if usage:
            token_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            token_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            details = usage.get("prompt_tokens_details") or {}
            if isinstance(details, dict):
                token_cached = int(details.get("cached_tokens") or 0)

        if not (token_in or token_out):
            for gen_list in (response.generations or []):
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) if msg is not None else None
                    if um:
                        token_in += int(um.get("input_tokens") or 0)
                        token_out += int(um.get("output_tokens") or 0)
                        details = um.get("input_token_details") or {}
                        if isinstance(details, dict):
                            token_cached += int(details.get("cache_read") or 0)
                        continue
                    gi = getattr(gen, "generation_info", None) or {}
                    gu = gi.get("token_usage") or {}
                    if gu:
                        token_in += int(gu.get("prompt_tokens") or gu.get("input_tokens") or 0)
                        token_out += int(gu.get("completion_tokens") or gu.get("output_tokens") or 0)

        units: dict[str, float] = {}
        # cached 部分单独记账（单价低）；非 cached 计入 token_in
        if token_cached:
            non_cached_in = max(0, token_in - token_cached)
            if non_cached_in:
                units["token_in"] = non_cached_in
            units["token_cached"] = token_cached
        elif token_in:
            units["token_in"] = token_in
        if token_out:
            units["token_out"] = token_out
        return units


class BillingToolCallbackHandler(AsyncCallbackHandler):
    """工具计费回调：每次工具调用结束时记一条 mcp_call。

    比起 monkey-patch 工具的 `_arun`/`coroutine`，走 callback 是 LangChain 推荐路径——
    它在 BaseTool.ainvoke 流程内被调用，不用关心工具签名（StructuredTool 的 config 必填、
    deepagents 注入的 runtime 参数等）就不会出问题。
    """

    def __init__(self, *, provider: str, tool_name: str) -> None:
        super().__init__()
        self._provider = provider
        self._tool_name = tool_name

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        try:
            await Billing.record(
                module="mcp",
                provider=self._provider,
                model=self._tool_name,
                units={"mcp_call": 1},
            )
        except Exception:
            logger.exception("[Billing] on_tool_end 处理异常（已吞掉）")

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        # 失败也记一条 success=0：便于"用户调用了多少次但失败了"对账；不记则失败成本完全黑掉
        try:
            await Billing.record(
                module="mcp",
                provider=self._provider,
                model=self._tool_name,
                units={"mcp_call": 1},
                success=0,
            )
        except Exception:
            logger.exception("[Billing] on_tool_error 处理异常（已吞掉）")
