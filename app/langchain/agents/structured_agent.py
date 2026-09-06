"""
结构化问答 Agent

直接使用 create_deep_agent() 的 response_format 参数，在单次运行中完成
工具调用（standard_query）+ 结构化输出，无需两阶段串联。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import httpx
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger
from pydantic import BaseModel

from app.langchain.llm_providers import get_llm
from app.langchain.tools.db_tools import make_db_tools
from app.settings.config import settings

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_TMP_IMAGES_DIR = _PROJECT_ROOT / ".tmp_images"
_EXTRACTION_LOG_DIR = _PROJECT_ROOT / ".extraction_log"
_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL


# ── 子 Agent 日志 Callback ────────────────────────────────────────────────────────

def _esc(text: Any) -> str:
    """转义 loguru colors 模式下的特殊字符，防止动态内容被误识别为颜色标签。"""
    return str(text).replace("\\", "\\\\").replace("<", r"\<").replace(">", r"\>")


class _SubAgentLogCallback(BaseCallbackHandler):
    """为子 agent 打印工具调用和返回日志，格式与主 agent 一致。"""

    def __init__(self, agent_name: str):
        super().__init__()
        self.agent_name = agent_name

    def _prefix(self) -> str:
        return f"\\<{self.agent_name}>"

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = _esc(serialized.get("name", "?"))
        safe = _esc(input_str)
        logger.opt(colors=True).info(f"  <white>{self._prefix()} [调用] {name}({safe})</white>")

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        name = _esc(kwargs.get("name", ""))
        safe = _esc(output)
        prefix = f"{self._prefix()} [返回] {name}: " if name else f"{self._prefix()} [返回] "
        logger.opt(colors=True).info(f"  <white>{prefix}{safe}</white>")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for gen_list in response.generations:
            for gen in gen_list:
                text = getattr(gen, "text", "") or ""
                if text:
                    logger.opt(colors=True).info(f"  <magenta>{self._prefix()} [思考] {_esc(text)}</magenta>")


def make_subagent_with_subagents(
        name: str,
        system_prompt: str,
        tools: list,
        subagents: Optional[List] = None,
        model: Any = None,
) -> Any:
    """
    创建支持嵌套子 agent 的 subagent runnable（使用 create_deep_agent）。
    适用于需要通过 task() 工具调用下级子 agent 的中间协调层。
    """
    import uuid as _uuid
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    resolved_model = model or get_llm()
    callback = _SubAgentLogCallback(agent_name=name)
    backend = FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True)

    kwargs: dict = {}
    if subagents:
        kwargs["subagents"] = subagents

    # deepagents 0.7 起 write_todos（TodoListMiddleware）不再默认内置。
    # 如需恢复：from langchain.agents.middleware import TodoListMiddleware，
    # 并在下方加 middleware=[TodoListMiddleware()]（调试后按需启用）。
    deep_agent = create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=MemorySaver(),
        backend=backend,
        debug=False,
        **kwargs,
    )

    class _WithCallback:
        def __init__(self, inner: Any):
            self._inner = inner

        def _cfg(self, kw: dict) -> dict:
            cfg = dict(kw.pop("config", None) or {})
            cfg["callbacks"] = cfg.get("callbacks", []) + [callback]
            cfg.setdefault("configurable", {}).setdefault("thread_id", str(_uuid.uuid4()))
            return cfg

        def invoke(self, state: dict, **kw: Any) -> Any:
            return self._inner.invoke(state, config=self._cfg(kw), **kw)

        async def ainvoke(self, state: dict, **kw: Any) -> Any:
            return await self._inner.ainvoke(state, config=self._cfg(kw), **kw)

        def stream(self, state: dict, **kw: Any):
            return self._inner.stream(state, config=self._cfg(kw), **kw)

        async def astream(self, state: dict, **kw: Any):
            async for chunk in self._inner.astream(state, config=self._cfg(kw), **kw):
                yield chunk

        def __getattr__(self, item: str) -> Any:
            return getattr(self._inner, item)

    return _WithCallback(deep_agent)


def make_subagent_runnable(
        name: str,
        system_prompt: str,
        tools: list,
        model: Any = None,
) -> Any:
    """
    创建带日志 callback 的子 agent runnable，供 CompiledSubAgent 使用。
    """
    from langchain.agents import create_agent
    from deepagents._models import resolve_model

    resolved_model = resolve_model(model or get_llm())
    callback = _SubAgentLogCallback(agent_name=name)

    runnable = create_agent(
        resolved_model,
        system_prompt=system_prompt,
        tools=tools,
    )

    # 包一层，注入 callback
    class _WithCallback:
        def __init__(self, inner: Any):
            self._inner = inner

        def _cfg(self, kwargs: dict) -> dict:
            cfg = dict(kwargs.pop("config", None) or {})
            cfg["callbacks"] = cfg.get("callbacks", []) + [callback]
            return cfg

        def invoke(self, state: dict, **kwargs: Any) -> Any:
            return self._inner.invoke(state, config=self._cfg(kwargs), **kwargs)

        async def ainvoke(self, state: dict, **kwargs: Any) -> Any:
            return await self._inner.ainvoke(state, config=self._cfg(kwargs), **kwargs)

        def stream(self, state: dict, **kwargs: Any):
            return self._inner.stream(state, config=self._cfg(kwargs), **kwargs)

        async def astream(self, state: dict, **kwargs: Any):
            async for chunk in self._inner.astream(state, config=self._cfg(kwargs), **kwargs):
                yield chunk

        # 透传其他属性（deepagents 可能访问 .config_specs 等）
        def __getattr__(self, item: str) -> Any:
            return getattr(self._inner, item)

    return _WithCallback(runnable)


# ── 图片下载工具 ─────────────────────────────────────────────────────────────────

_img_counter = 0


def _make_fetch_image_tool():
    """创建图片下载工具，下载 URL 图片到 .tmp_images/ 并返回虚拟路径供 read_file 使用。"""

    @tool
    async def fetch_image(urls: str) -> str:
        """下载图片到本地，返回可供 read_file 读取的虚拟路径，支持批量。

        Args:
            urls: 图片 URL 或纯文件名，多个用逗号分隔。
                  支持完整 URL（https://...）和纯文件名（如 abc123.jpg），
                  纯文件名会自动补全为完整 URL。

        Returns:
            单个时返回路径字符串；多个时返回 JSON 数组，如 ["/.tmp_images/a.jpg", "/.tmp_images/b.jpg"]
        """
        import json as _json

        await asyncio.to_thread(_TMP_IMAGES_DIR.mkdir, exist_ok=True)

        async def _download(client: httpx.AsyncClient, url: str) -> str:
            global _img_counter
            _img_counter += 1
            if not url.startswith(("http://", "https://")):
                url = _IMAGE_BASE_URL + url.lstrip("/")
            filename = url.split("/")[-1].split("?")[0] or f"img_{_img_counter}.jpg"
            if not any(filename.lower().endswith(e) for e in (".png", ".jpeg", ".jpg", ".gif", ".webp")):
                filename += ".jpg"
            local_path = _TMP_IMAGES_DIR / filename
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                await asyncio.to_thread(local_path.write_bytes, resp.content)
                return f"/.tmp_images/{filename}"
            except Exception as e:
                return f"下载失败: {e}"

        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if len(url_list) == 1:
                return await _download(client, url_list[0])
            results = await asyncio.gather(*[_download(client, u) for u in url_list])
        return _json.dumps(results, ensure_ascii=False)

    return fetch_image

_DEFAULT_REASONING_PROMPT = """\
你是一位标准化领域的专业问答助手。
你可以查询标准数据库，回答用户关于标准的任何问题。

## 可用数据库表

### standard_base_info — 标准基本信息
关键字段：
- standard_no         标准号（查询主键）
- cname               标准名称
- use_range           适用范围
- std_nature          标准性质（如：强制性/推荐性）
- std_type            标准类型（如：国家标准/行业标准/团体标准）
- state               标准状态（如：现行/废止/在研）
- issue_date          发布日期
- act_date            实施日期
- replace_stds        本标准替代的旧标准
- target_stds         替代本标准的新标准
- adopt_std_no        对应的国际标准号
- adopt_level         采用程度（如：等同/修改/非等效）

### standard_jgh_pdf + standard_jgh_pdf_chapter — 标准正文（两表关联使用）
先从 standard_jgh_pdf 按 standard_no 查出 main_task_id，再从 standard_jgh_pdf_chapter 按章节读取正文。
关键字段：
- standard_jgh_pdf.standard_no       标准号
- standard_jgh_pdf.main_task_id      关联键
- standard_jgh_pdf_chapter.title     章节标题
- standard_jgh_pdf_chapter.title_no  章节编号（如：1、2.1、附录A）
- standard_jgh_pdf_chapter.word      章节文本内容

### standard_cache_sim — 全文文本相似度比对结果
关键字段：
- source_standard_no       源标准编号
- target_standard_no       目标标准编号
- similarity_percentage    相似度百分比（0~100）
- is_valid                 缓存是否有效（查询时需过滤 is_valid=1）

### standard_cache_ai — AI 指标比对结果
关键字段：
- source_standard_no       源标准编号
- target_standard_no       目标标准编号
- relationship             两标准的关系结论
- matched_indicators       匹配的指标对比表（JSON）
- source_unique_indicators 源标准独有指标（JSON）
- target_unique_indicators 目标标准独有指标（JSON）
- is_valid                 缓存是否有效（查询时需过滤 is_valid=1）

## 可用工具

  standard_tables()          — 列出数据库中所有标准表
  standard_schema(table)     — 查看指定表的字段结构（仅限 standard_ 前缀表）
  standard_query(sql)        — 执行只读 SQL（仅限 standard_ 前缀表；需含 LIMIT；is_valid 字段需过滤 = 1）
  get_standard_chapters(standard_no, toc_only?, title_no_prefix?, keyword?, limit?, offset?)
                             — 查询标准正文章节；建议先用 toc_only=True 获取目录及各章 word_count，
                               再按 title_no_prefix 或 keyword 定向读取正文，支持 limit/offset 分页
"""

_IMAGE_HANDLING_PROMPT = """\

## 图片与文档处理

- 遇到 `<img>` 标签（src 可能是完整 URL 或纯文件名）：
  1. 优先调用 `parse_with_mineru(filename=...)`
- **如果你是调度型 agent（负责调用子 agent），不要自己处理图片或文档，直接将任务派发给子 agent，由子 agent 负责调用上述工具。**
"""


# 效果不佳时使用
# 1. `fetch_image(url=...)` 下载图片（传完整 URL 或纯文件名均可，纯文件名会自动补全），得到虚拟路径
# 2. `read_file(file_path=...)` 读取图片，返回多模态内容


class StructuredQAAgent:
    """
    结构化问答 Agent。

    使用 create_deep_agent() 的 response_format，单次 pipeline 完成工具调用和结构化输出。

    Usage:
        class MyResult(BaseModel):
            summary: str
            standard_nos: list[str]

        agent = create_structured_qa_agent(
            db_path="/path/to/db.sqlite3",
            output_schema=MyResult,
            system_prompt="...",
        )

        result: MyResult = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "..."}]},
            config={"configurable": {"thread_id": "abc"}},
        )
    """

    def __init__(self, deep_agent, output_schema: Type[BaseModel]):
        self._deep_agent = deep_agent
        self._output_schema = output_schema

    async def ainvoke(
        self,
        input: Dict[str, Any],
        config: Optional[Dict] = None,
    ) -> BaseModel:
        # 确保临时目录存在（同步文件系统调用扔到线程池，避免阻塞 event loop）
        await asyncio.to_thread(_TMP_IMAGES_DIR.mkdir, exist_ok=True)
        await asyncio.to_thread(_EXTRACTION_LOG_DIR.mkdir, exist_ok=True)
        try:
            return await self._run(input, config)
        finally:
            # 清理临时图片（rmtree 可能耗时较久，必须放线程池）
            if _TMP_IMAGES_DIR.exists():
                await asyncio.to_thread(shutil.rmtree, _TMP_IMAGES_DIR, ignore_errors=True)

    async def _run(
            self,
            input: Dict[str, Any],
            config: Optional[Dict] = None,
    ) -> BaseModel:
        logger.info("StructuredQAAgent 推理中（流式，工具调用 + 结构化输出）...")
        _thinking_buf: list[str] = []

        _thinking_flush_threshold = 400

        def _flush_thinking(force: bool = False):
            if not _thinking_buf:
                return
            text = "".join(_thinking_buf).strip()
            if not text:
                _thinking_buf.clear()
                return
            if not force and len(text) < _thinking_flush_threshold:
                return
            # 完整输出思考内容
            logger.opt(colors=True).info(f"  <magenta>[思考] {_esc(text)}</magenta>")
            _thinking_buf.clear()

        async for stream_mode, chunk in self._deep_agent.astream(
                input, config=config or {}, stream_mode=["messages", "updates"]
        ):
            if stream_mode == "messages":
                token, metadata = chunk
                # 过滤 LangGraph 对话历史压缩产物（如 "## SESSION INTENT"），避免混入思考日志
                if metadata.get("lc_source") == "summarization":
                    continue
                node = metadata.get("langgraph_node", "")
                content = getattr(token, "content", "") or ""
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                if node == "model" and content:
                    _thinking_buf.append(content)
                    _flush_thinking(force=False)

            elif stream_mode == "updates":
                _flush_thinking(force=True)
                for node, node_update in chunk.items():
                    if node == "tools":
                        for msg in (node_update.get("messages") or []):
                            tool_name = getattr(msg, "name", "") or ""
                            content = getattr(msg, "content", "") or ""
                            if isinstance(content, list):
                                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                            if tool_name:
                                logger.opt(colors=True).info(
                                    f"  <white>[返回] {_esc(tool_name)}: {_esc(content)}</white>")
                    elif node == "model":
                        for msg in (node_update.get("messages") or []):
                            tool_calls = getattr(msg, "tool_calls", None) or []
                            for tc in tool_calls:
                                name = tc.get("name", "?")
                                args = tc.get("args", {})
                                tc_id = tc.get("id", "")[:8]
                                logger.opt(colors=True).info(
                                    f"  <white>[调用] {_esc(name)}({_esc(args)}) [{tc_id}]</white>")

        _flush_thinking(force=True)
        state = await self._deep_agent.aget_state(config=config or {})
        structured = state.values.get("structured_response")
        if structured is None:
            logger.warning("流式未获取到 structured_response，fallback 到 ainvoke")
            result = await self._deep_agent.ainvoke(input, config=config or {})
            structured = result.get("structured_response") if isinstance(result, dict) else None

        if structured is None:
            # 最终兜底：从最后一条 AI 消息中提取结构化输出
            logger.warning("尝试从最后一条 AI 消息中提取结构化输出...")
            messages = state.values.get("messages", [])
            last_ai_content = None
            for msg in reversed(messages):
                if "AIMessage" in type(msg).__name__:
                    content = getattr(msg, "content", "") or ""
                    if isinstance(content, str) and content.strip():
                        last_ai_content = content
                        break
            if last_ai_content:
                try:
                    from app.langchain.llm_providers import get_llm
                    structured = await get_llm().with_structured_output(self._output_schema).ainvoke(
                        f"请将以下分析结果整理为结构化输出：\n\n{last_ai_content}"
                    )
                    logger.info("从 AI 消息中提取结构化输出成功")
                except Exception as e:
                    logger.error(f"从 AI 消息提取结构化输出失败: {e}")

        if structured is None:
            raise ValueError("Agent 未返回 structured_response，且无法从消息中提取结构化输出")
        logger.info(f"推理完成，输出类型：{type(structured).__name__}")
        return structured


def make_default_tools(pool_id: Optional[int] = None) -> list:
    """返回所有 agent 通用的默认工具列表（db_tools + fetch_image + parse_with_mineru）。"""
    from app.langchain.tools.mineru_tools import parse_with_mineru
    return make_db_tools(pool_id) + [_make_fetch_image_tool(), parse_with_mineru]


def create_structured_qa_agent(
    output_schema: Type[BaseModel],
    *,
    system_prompt: Optional[str] = None,
    skills: Optional[List[str]] = None,
        subagents: Optional[List] = None,
    extra_tools: Optional[List] = None,
    pool_id: Optional[int] = None,
) -> StructuredQAAgent:
    """
    创建结构化问答 Agent。

    Args:
        output_schema:         Pydantic 模型，定义期望的 JSON 输出结构
        system_prompt:         推理的系统提示，不传则使用默认标准问答 prompt
        skills:                skill 目录列表
        subagents:             子 agent 列表（SubAgent TypedDict）
        extra_tools:           额外追加的工具列表（如 get_cached_indicators）
        pool_id:               查重池ID，None 表示不限制
    """
    from deepagents import create_deep_agent

    llm = get_llm()
    tools = make_default_tools(pool_id)
    if extra_tools:
        tools = tools + extra_tools

    tool_name = output_schema.__name__
    output_instruction = (
        f"\n\n## 最终输出规范（必须遵守）\n\n"
        f"完成所有分析后，你必须调用 `{tool_name}` 工具提交最终的结构化结果，"
        f"不能仅以文字回复结束。"
    )

    image_section = _IMAGE_HANDLING_PROMPT
    final_system_prompt = (system_prompt or _DEFAULT_REASONING_PROMPT) + image_section + output_instruction

    from deepagents.backends import FilesystemBackend
    backend = FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True)

    kwargs: dict = {}
    if skills:
        kwargs["skills"] = skills
    if subagents:
        kwargs["subagents"] = subagents

    # ── deepagents 0.7 可选恢复项（调试后按需启用）──────────────────────────
    # 1) write_todos 规划工具：0.7 起不再默认内置。指标提取等提示词里强制要求
    #    write_todos 的 agent 走本函数，如调试发现模型调用不存在的工具，
    #    取消下方 middleware 注释即可恢复：
    #    from langchain.agents.middleware import TodoListMiddleware
    # 2) 0.7 移除的官方 base agent 提示词：本项目已用自写 system_prompt 覆盖，
    #    一般无需恢复；如确需，可取 deepagents.graph.BASE_AGENT_PROMPT
    #    （已废弃，0.9 移除）拼到 system_prompt 前面。
    deep_agent = create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=final_system_prompt,
        response_format=ToolStrategy(
            schema=output_schema,
            handle_errors=True,
        ),
        checkpointer=MemorySaver(),
        backend=backend,
        debug=False,
        # middleware=[TodoListMiddleware()],  # write_todos：0.7 起默认移除，按需取消注释
        **kwargs,
    )

    return StructuredQAAgent(deep_agent=deep_agent, output_schema=output_schema)


__all__ = ["create_structured_qa_agent", "StructuredQAAgent", "make_subagent_runnable", "make_subagent_with_subagents"]
