"""
Agent 通用日志 Callback：把工具调用、返回、思考流打到控制台。
从 structured_agent 抽出，供 qa_agent 等共用。
"""

from __future__ import annotations

import time
from typing import Any, List, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from loguru import logger


def _esc(text: Any) -> str:
    """转义 loguru colors 模式下的特殊字符。"""
    return str(text).replace("\\", "\\\\").replace("<", r"\<").replace(">", r"\>")


def _count_chars(messages: List[List[BaseMessage]]) -> tuple[int, int]:
    """返回 (文本字符数, 图片base64字符数)"""
    text_chars = 0
    image_chars = 0
    for batch in messages:
        for m in batch:
            if isinstance(m.content, str):
                text_chars += len(m.content)
            elif isinstance(m.content, list):
                for part in m.content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            text_chars += len(part.get("text", ""))
                        elif part_type in ("image_url", "image"):
                            # 统计 image_url.url 或 image.source.data 的长度
                            if "image_url" in part:
                                url = part["image_url"].get("url", "")
                                if url.startswith("data:image"):
                                    image_chars += len(url)
                                else:
                                    text_chars += len(url)
                            elif "source" in part:
                                data = part["source"].get("data", "")
                                image_chars += len(data)
                            else:
                                # 兜底：整个 part 算图片
                                image_chars += len(str(part))
                        else:
                            text_chars += len(str(part.get("text", part.get("content", ""))))
    return text_chars, image_chars


def _fmt_messages(messages: List[List[BaseMessage]]) -> str:
    """把消息列表格式化为易读的单字符串，用于原始日志输出。"""
    lines = []
    for batch_idx, batch in enumerate(messages):
        for m in batch:
            role = getattr(m, "type", m.__class__.__name__)
            if isinstance(m.content, str):
                body = m.content
            elif isinstance(m.content, list):
                parts = []
                for part in m.content:
                    if isinstance(part, dict):
                        t = part.get("type", "")
                        if t == "text":
                            parts.append(part.get("text", ""))
                        elif t in ("image_url", "image"):
                            parts.append(f"[{t}: {str(part)[:80]}...]")
                        else:
                            parts.append(str(part)[:200])
                    else:
                        parts.append(str(part)[:200])
                body = "\n".join(parts)
            else:
                body = str(m.content)
            tool_calls = getattr(m, "tool_calls", None)
            tc_str = ""
            if tool_calls:
                tc_str = f"  tool_calls: {[{'name': tc.get('name'), 'args': tc.get('args')} for tc in tool_calls]}"
            lines.append(f"  [{role}] {body}{tc_str}")
    return "\n".join(lines)


class AgentLogCallback(BaseCallbackHandler):
    """打印工具调用 / 返回 / LLM 收发原始内容和耗时，按 agent_name 加前缀。"""

    def __init__(self, agent_name: str = "qa", enabled: bool = True):
        super().__init__()
        self.agent_name = agent_name
        self.enabled = enabled
        self._llm_start_times: dict[str, float] = {}
        self._tool_start_times: dict[str, float] = {}

    def _prefix(self) -> str:
        return f"\\<{self.agent_name}>"

    # ── LLM 发送 ──────────────────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: dict,
        prompts: List[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._llm_start_times[str(run_id)] = time.monotonic()
        if not self.enabled:
            return
        model = serialized.get("kwargs", {}).get("model_name") or serialized.get("name", "?")
        logger.opt(colors=True).info(
            f"  <cyan>{self._prefix()} [LLM→发送] model={_esc(model)} "
            f"prompts={len(prompts)} chars={sum(len(p) for p in prompts)}</cyan>"
        )
        for i, p in enumerate(prompts):
            logger.opt(colors=True).debug(
                f"  <cyan>{self._prefix()} [LLM→prompt[{i}]]\n{_esc(p)}</cyan>"
            )

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: List[List[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._llm_start_times[str(run_id)] = time.monotonic()
        model = serialized.get("kwargs", {}).get("model_name") or serialized.get("name", "?")
        msg_count = sum(len(b) for b in messages)
        text_chars, image_chars = _count_chars(messages)
        total_chars = text_chars + image_chars

        if image_chars > 0:
            image_pct = (image_chars / total_chars * 100) if total_chars > 0 else 0
            logger.opt(colors=True).info(
                f"  <cyan>{self._prefix()} [LLM→发送] model={_esc(model)} "
                f"messages={msg_count} total_chars={total_chars} "
                f"(text={text_chars} image_base64={image_chars} {image_pct:.1f}%)</cyan>"
            )
        else:
            logger.opt(colors=True).info(
                f"  <cyan>{self._prefix()} [LLM→发送] model={_esc(model)} "
                f"messages={msg_count} total_chars={total_chars}</cyan>"
            )

        if not self.enabled:
            return
        # 原始消息明细（DEBUG 级）
        logger.opt(colors=True).debug(
            f"  <cyan>{self._prefix()} [LLM→原始消息]\n{_esc(_fmt_messages(messages))}</cyan>"
        )

    # ── LLM 接收 ──────────────────────────────────────────────────────────────

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = time.monotonic() - self._llm_start_times.pop(str(run_id), time.monotonic())
        total_out_chars = 0
        usage = response.llm_output.get("token_usage", {}) if response.llm_output else {}

        for gen_list in response.generations:
            for gen in gen_list:
                text = getattr(gen, "text", "") or ""
                total_out_chars += len(text)

        prompt_tokens = usage.get("prompt_tokens", "?")
        completion_tokens = usage.get("completion_tokens", "?")
        logger.opt(colors=True).info(
            f"  <green>{self._prefix()} [LLM←完成] elapsed={elapsed:.2f}s "
            f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
            f"out_chars={total_out_chars}</green>"
        )

        if not self.enabled:
            return

        for gen_list in response.generations:
            for gen in gen_list:
                text = getattr(gen, "text", "") or ""
                # tool_calls 在 message 上
                msg = getattr(gen, "message", None)
                tcs = getattr(msg, "tool_calls", None)
                reasoning = (getattr(msg, "additional_kwargs", {}) or {}).get("reasoning_content", "")
                if reasoning:
                    logger.opt(colors=True).debug(
                        f"  <magenta>{self._prefix()} [LLM←思考]\n{_esc(reasoning)}</magenta>"
                    )
                if text:
                    logger.opt(colors=True).info(
                        f"  <magenta>{self._prefix()} [LLM←回复] {_esc(text)}</magenta>"
                    )
                if tcs:
                    logger.opt(colors=True).info(
                        f"  <magenta>{self._prefix()} [LLM←tool_calls] "
                        f"{_esc([{'name': tc.get('name'), 'args': tc.get('args')} for tc in tcs])}</magenta>"
                    )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = time.monotonic() - self._llm_start_times.pop(str(run_id), time.monotonic())
        if not self.enabled:
            return
        logger.opt(colors=True).error(
            f"  <red>{self._prefix()} [LLM×错误] elapsed={elapsed:.2f}s {_esc(error)}</red>"
        )

    # ── 工具调用 ──────────────────────────────────────────────────────────────

    def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._tool_start_times[str(run_id)] = time.monotonic()
        if not self.enabled:
            return
        name = _esc(serialized.get("name", "?"))
        safe = _esc(input_str)
        logger.opt(colors=True).info(f"  <white>{self._prefix()} [工具→] {name}({safe})</white>")

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = time.monotonic() - self._tool_start_times.pop(str(run_id), time.monotonic())
        if not self.enabled:
            return
        name = _esc(kwargs.get("name", ""))
        safe = _esc(str(output))
        prefix = f"{self._prefix()} [工具←] {name} elapsed={elapsed:.2f}s: " if name else f"{self._prefix()} [工具←] elapsed={elapsed:.2f}s "
        logger.opt(colors=True).info(f"  <white>{prefix}{safe}</white>")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = time.monotonic() - self._tool_start_times.pop(str(run_id), time.monotonic())
        if not self.enabled:
            return
        name = _esc(kwargs.get("name", ""))
        logger.opt(colors=True).warning(
            f"  <yellow>{self._prefix()} [工具×] {name} elapsed={elapsed:.2f}s {_esc(error)}</yellow>"
        )


__all__ = ["AgentLogCallback"]
