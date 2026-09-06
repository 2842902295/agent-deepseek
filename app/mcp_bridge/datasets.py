"""
数据集 MCP 服务桥：把「数据集」从展示空壳改造成进程内真实 MCP 服务。

三个系统数据集（agent_connector 登记行，kind=dataset；connector_key 与下方
SYSTEM_DATASETS 的 key 一一对应）：
  - 条款集   dataset_clause  → /mcp-bridge/dataset-clause/{uid}/mcp
  - 术语集   dataset_term    → /mcp-bridge/dataset-term/{uid}/mcp
  - 标准元数据集 dataset_meta → /mcp-bridge/dataset-meta/{uid}/mcp

双通道：
  - 向量通道：统一向量库服务（app/services/vector_hub）的系统库
    standard_chapter / standard_term / standard_meta
  - 常规通道：standard_ 系列表的结构化 SQL 查询（章节阅读 / 术语检索 / 元数据检索）；
    标准元数据集另挂灵活只读 SQL 三件（standard_query / standard_tables /
    standard_schema，复用 db_tools 安全门：仅单条 SELECT、仅 standard_ 前缀表、
    自动 LIMIT 2000 兜底），全量统计 / 分布分析 / 表结构探索走这里

启用语义与连接器完全一致（理论上就是三个独立的 MCP 服务，进程内托管只是部署
便利）：用户在技能面板添加并启用后，qa_agent 构建期才把对应服务挂进会话——
qa_agent.py 的连接器循环识别这三个系统 key，把商店登记地址改写为 per-uid
回环端点；其余走通用 streamable-http 分支。stdtools 桥（dsh_http_bridge /
dsh_tools_server）里的标准数据内置工具已同步摘除，标准数据访问一律经由本模块。

per-uid 挂载的原因：工具调用前需从活跃回合表（dsh_http_bridge._ACTIVE_TURNS，
qa.py/scheduler/sediment 回合开始登记）重建 AgentCallContext——get_standard_chapters
的媒体下载 / 多模态内联依赖 workspace。另有不带 uid 的 /mcp 端点供商店「试连」。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from contextvars import ContextVar
from typing import Annotated, Any, List, Optional

import aiomysql
import mcp.types as types
from langchain.tools import tool
from loguru import logger
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.services.mysql_pool import standard_pool
from app.services.vector_hub.search import search_service

# ── 数据集登记表（connector_key 与 agent_connector 行一致） ─────────────────────

SYSTEM_DATASETS: list[dict] = [
    {
        "key": "dataset_clause",
        "mount": "dataset-clause",
        "title": "条款集",
        "seed_desc": "平台提供：标准条款语义检索（章节级）+ 标准正文章节阅读（含表格/公式/图片随取）+ 两标准章节级差异映射。向量与常规双通道；在技能面板添加并启用后对话中生效。",
    },
    {
        "key": "dataset_term",
        "mount": "dataset-term",
        "title": "术语集",
        "seed_desc": "平台提供：标准术语语义检索与结构化查询（术语名 / 英文 / 释义），源标准术语表。向量与常规双通道；在技能面板添加并启用后对话中生效。",
    },
    {
        "key": "dataset_meta",
        "mount": "dataset-meta",
        "title": "标准元数据集",
        "seed_desc": "平台提供：相似标准语义检索 + 标准元数据结构化查询（标准号 / 名称 / 状态 / 日期 / 替代关系）+ 灵活只读统计。向量与常规双通道；在技能面板添加并启用后对话中生效。",
    },
]
SYSTEM_DATASET_KEYS = frozenset(d["key"] for d in SYSTEM_DATASETS)

# 常规通道查询保护
_TERM_WORD_EXCERPT = 1000  # 术语释义摘录字符数
_QUERY_LIMIT_DEFAULT = 800  # 单次结构化查询行数默认值（推荐值，不硬拦：
# agent 需要更多时显式传大 limit 即可拿到；统计/聚合类需求仍应走 standard_query SQL）
# 元数据返回口径：整行全字段（只剔纯系统列 + 空值），不维护手工字段子集，避免「漏字段」
_META_DROP_COLUMNS = {"id", "deleted", "creator", "updater", "create_time", "update_time", "is_relate_standard_jgh", "is_relate_standard_pdf"}
# 桥层文本兜底上限（最后一道保险）：内层工具已各自控体积（standard_query 1200k 封顶等），
# 此处放宽到 1400k 避免正常大结果被拦腰截断成碎 JSON、agent 被迫多轮重查拼数据
_BRIDGE_TEXT_CAP = 1400000


# ── 常规通道：结构化查询实现 ────────────────────────────────────────────────────


async def _load_term_word_map(term_ids: List) -> dict:
    """按 term_id 批量取术语释义摘录（向量库 payload 不含释义全文，回源表补）。"""
    ids = sorted({int(i) for i in term_ids if i is not None})
    if not ids:
        return {}
    out: dict = {}
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(
                    f"SELECT id, LEFT(IFNULL(word,''),{_TERM_WORD_EXCERPT}) AS word_excerpt "
                    f"FROM standard_jgh_pdf_term WHERE id IN ({ph})",
                    chunk,
                )
                for r in await cur.fetchall():
                    out[int(r["id"])] = r["word_excerpt"] or ""
    return out


@tool
async def search_terms_vector(
    query: Annotated[str, "术语相关的查询文本（术语名称、英文、释义方向均可）"],
    top_k: Annotated[int, "返回条数，按任务需要自行决定（广撒网/盘点类任务可放大，精确查找用小值），无固定上限"] = 100,
    scope_standard_nos: Annotated[
        Optional[str],
        "限定检索范围的标准号，多个用逗号分隔；不传则在全库术语中检索",
    ] = None,
    min_score: Annotated[float, "最低相关度阈值，低于则过滤"] = 0.5,
) -> str:
    """
    在标准术语语义库上做术语级语义召回（向量通道）。
    返回相似度从高到低的术语（含 standard_no / term_id / title / title_ename /
    chapter_no / word_excerpt 释义摘录 / cosine_score，越接近 1 越相似）。
    """
    try:
        scope_list: Optional[List[str]] = None
        if scope_standard_nos:
            scope_list = [s.strip() for s in scope_standard_nos.split(",") if s.strip()] or None

        rows = await search_service.search(
            ["standard_term"],
            query,
            top_k=int(top_k),
            ref_in=scope_list,
        )
        kept = [r for r in rows if r["score"] >= min_score]
        word_map = await _load_term_word_map([(r["payload"] or {}).get("term_id") for r in kept])
        results = []
        for r in kept:
            p = r["payload"] or {}
            tid = p.get("term_id")
            results.append(
                {
                    "standard_no": p.get("standard_no") or r["refKey"],
                    "term_id": tid,
                    "title": p.get("title"),
                    "title_ename": p.get("title_ename"),
                    "chapter_no": p.get("chapter_no"),
                    "word_excerpt": word_map.get(int(tid), "") if tid is not None else "",
                    "cosine_score": r["score"],
                }
            )
        return json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.exception("[search_terms_vector] failed")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
async def search_terms(
    standard_no: Annotated[Optional[str], "限定标准编号，如 GB/T 1234-2020"] = None,
    keyword: Annotated[Optional[str], "在术语名 / 英文 / 释义中模糊匹配的关键词"] = None,
    limit: Annotated[int, "最大返回条数，默认 800；需要更多时直接传更大的值"] = _QUERY_LIMIT_DEFAULT,
) -> str:
    """
    结构化术语查询（常规通道）：按标准号和/或关键词在术语源数据中检索。
    参数均可省略：都不传 = 全库术语浏览（默认前 800 条），统计/聚合类需求
    （术语总量、按标准分布等）请用标准元数据集的 standard_query 走 SQL。
    返回 standard_no / term_id / title / title_ename / chapter_no / word_excerpt（释义摘录）。
    需要按语义找相近术语时用 search_terms_vector。
    """
    try:
        standard_no = (standard_no or "").strip()
        keyword = (keyword or "").strip()
        limit = max(1, int(limit or _QUERY_LIMIT_DEFAULT))

        conditions = ["t.word IS NOT NULL", "t.word <> ''", "IFNULL(t.deleted,0)=0"]
        params: list = []
        if standard_no:
            conditions.append("p.standard_no = %s")
            params.append(standard_no)
        if keyword:
            conditions.append("(t.title LIKE %s OR t.title_ename LIKE %s OR t.word LIKE %s)")
            params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]

        sql = (
            "SELECT p.standard_no, t.id AS term_id, t.title, t.title_ename, t.chapter_no, "
            f"LEFT(IFNULL(t.word,''),{_TERM_WORD_EXCERPT}) AS word_excerpt "
            "FROM standard_jgh_pdf_term t "
            "JOIN standard_jgh_pdf p ON p.main_task_id = t.main_task_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY p.standard_no, t.id "
            "LIMIT %s"
        )
        params.append(limit)
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = list(await cur.fetchall())
        return json.dumps(
            {"ok": True, "count": len(rows), "results": rows},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[search_terms] failed")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
async def search_standards(
    standard_no: Annotated[Optional[str], "精确匹配的标准编号，如 GB/T 1234-2020"] = None,
    keyword: Annotated[Optional[str], "在标准中文名（cname）/ 英文名（ename）中模糊匹配的关键词（国际标准无中文名，靠英文名命中）"] = None,
    state: Annotated[Optional[str], "现行有效性状态模糊过滤，如「现行」「废止」"] = None,
    limit: Annotated[int, "最大返回条数，默认 800；需要更多时直接传更大的值"] = _QUERY_LIMIT_DEFAULT,
) -> str:
    """
    标准元数据结构化查询（常规通道）：按标准号 / 名称关键词 / 状态查标准元数据。
    参数均可省略：都不传 = 全库标准浏览（默认前 800 条），统计/聚合类需求
    （数量、按年份/行业/状态分布等）请用 standard_query 走 SQL。
    返回标准的全部元数据字段（标准号 / 中英文名 / 适用范围 / 性质 / 状态 /
    各日期 / 归口与起草单位 / 分类与领域 / 采标与替代关系等，空字段剔除）。
    需要按语义找相似标准时用 vector_search_standards_ob。
    """
    try:
        standard_no = (standard_no or "").strip()
        keyword = (keyword or "").strip()
        state = (state or "").strip()
        limit = max(1, int(limit or _QUERY_LIMIT_DEFAULT))

        conditions = ["IFNULL(deleted,0)=0"]
        params: list = []
        if standard_no:
            conditions.append("standard_no = %s")
            params.append(standard_no)
        if keyword:
            conditions.append("(cname LIKE %s OR ename LIKE %s)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        if state:
            conditions.append("state LIKE %s")
            params.append(f"%{state}%")

        sql = (
            "SELECT * FROM standard_base_info "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY standard_no "
            "LIMIT %s"
        )
        params.append(limit)
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = [
                    {k: v for k, v in r.items() if k not in _META_DROP_COLUMNS and v is not None and v != ""}
                    for r in await cur.fetchall()
                ]
        return json.dumps(
            {"ok": True, "count": len(rows), "results": rows},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[search_standards] failed")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


# ── 工具装配（懒加载 + 缓存：复用既有 langchain 工具，行为零分叉） ──────────────

_tools_cache: Optional[dict[str, list]] = None


def _dataset_tools_map() -> dict[str, list]:
    """构建三个数据集的工具表（首次调用时构建并缓存）。

    条款集 / 元数据集直接复用 db_tools / vector_oceanbase_tools 的既有 @tool
    （工具名 / 入参 / 返回 JSON 是稳定契约），术语集为本模块新写的两个工具。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    from app.langchain.tools.db_tools import make_db_tools
    from app.langchain.tools.vector_oceanbase_tools import make_vector_ob_tools

    db = {t.name: t for t in make_db_tools()}
    vec = {t.name: t for t in make_vector_ob_tools()}
    _tools_cache = {
        # 条款集：语义召回章节 + 正文章节阅读（含表格/公式/图片媒体随取）+ 章节级差异映射
        "dataset_clause": [
            vec["vector_search_chapters"],
            db["get_standard_chapters"],
            vec["vector_compare_standards"],
        ],
        # 术语集：语义召回术语 + 结构化术语检索
        "dataset_term": [search_terms_vector, search_terms],
        # 标准元数据集：语义召回标准 + 结构化元数据检索 + 灵活只读 SQL（全量统计 / 表结构探索）。
        # SQL 三件复用 db_tools 既有实现（安全门在 _exec_query 执行口统一拦截，
        # pool_id=None 不限范围），工具名 / 入参 / 返回 JSON 与历史契约一致
        "dataset_meta": [
            vec["vector_search_standards_ob"],
            search_standards,
            db["standard_query"],
            db["standard_tables"],
            db["standard_schema"],
        ],
    }
    return _tools_cache


def _tool_schema(t) -> dict:
    # tool_json_schema：给 bool / array / object 参数加 string 备选——MCP SDK 在
    # call_tool 前按 inputSchema 做 jsonschema 校验，不加备选字符串化入参会被拦死
    from app.langchain.tools._tool_args import tool_json_schema

    return tool_json_schema(t)


# ── MCP Server 工厂（低层 Server + 请求级 uid 上下文） ─────────────────────────

_CTX_DATASET_UID: ContextVar[Optional[int]] = ContextVar("dataset_bridge_uid", default=None)


def _make_dataset_server(key: str, title: str) -> Server:
    """为一个数据集建低层 MCP Server（list/call 共用该数据集的固定工具表）。"""
    server = Server(key)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        tools = _dataset_tools_map()[key]
        return [types.Tool(name=t.name, description=(t.description or "")[:2000], inputSchema=_tool_schema(t)) for t in tools]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        tools = _dataset_tools_map()[key]
        tool_obj = next((t for t in tools if t.name == name), None)
        if tool_obj is None:
            return [types.TextContent(type="text", text=f"未知工具：{name}")]

        uid = _CTX_DATASET_UID.get()

        # 逐消息调用上下文：从活跃回合表重建（章节媒体下载 / 多模态内联依赖 workspace）
        try:
            from app.mcp_bridge.dsh_http_bridge import get_active_turn
            from app.services.agent_runtime.call_context import AgentCallContext, default_user_workspace, set_agent_call_context

            turn = get_active_turn(uid)
            # 工作区兜底：回合条目缺失时按 uid 推导（workspace 对 uid 是纯函数）
            _ws = turn.get("workspace") or (default_user_workspace(uid) if uid is not None else None)
            set_agent_call_context(
                AgentCallContext(
                    session_id=turn.get("session_id"),
                    session_key=turn.get("session_key"),
                    message_id=turn.get("message_id"),
                    workspace_dir=_ws,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[datasets:{key}] AgentCallContext 重建失败: {e}")

        _uid_token = None
        try:
            if uid is not None:
                from app.core.ctx import CTX_USER_ID

                _uid_token = (CTX_USER_ID, CTX_USER_ID.set(uid))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[datasets:{key}] CTX_USER_ID 设置失败: {e}")

        try:
            try:
                result = await tool_obj.ainvoke(dict(arguments or {}))
            except NotImplementedError:
                result = await asyncio.to_thread(tool_obj.invoke, dict(arguments or {}))
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[datasets:{key}] 工具 {name} 执行失败")
            return [types.TextContent(type="text", text=f"工具执行失败：{e}")]
        finally:
            if _uid_token is not None:
                try:
                    _uid_token[0].reset(_uid_token[1])
                except Exception:  # noqa: BLE001
                    pass
            try:
                from app.services.agent_runtime.call_context import clear_agent_call_context

                clear_agent_call_context()
            except Exception:  # noqa: BLE001
                pass

        # get_standard_chapters 对视觉块用户可能返回多模态 content blocks 列表：
        # dsh 无图片通道（附件服务缺件），只取文本块回传，行为与 stdtools 桥一致
        if isinstance(result, list):
            text = "\n".join(b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = result if isinstance(result, str) else str(result)
        if len(text) > _BRIDGE_TEXT_CAP:
            text = text[:_BRIDGE_TEXT_CAP] + "\n...[内容过长已截断，请缩小查询范围或使用分页参数]"
        return [types.TextContent(type="text", text=text)]

    logger.info(f"[datasets] MCP 服务构建完成：{title}（{key}）")
    return server


# ── ASGI 挂载 ─────────────────────────────────────────────────────────────────

_DATASET_BRIDGES: List[tuple] = []  # (key, Server, manager)
_DATASET_STACK: Optional[contextlib.AsyncExitStack] = None


class _DatasetUidHandler:
    """ASGI 入口：从 URL 解析 uid（/mcp 试连端点无 uid）注入 contextvar。"""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:
        uid_raw = (scope.get("path_params") or {}).get("uid")
        try:
            uid = int(uid_raw) if uid_raw is not None else None
        except (TypeError, ValueError):
            uid = None
        token = _CTX_DATASET_UID.set(uid)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _CTX_DATASET_UID.reset(token)


def mount_dataset_bridges(app: Any) -> None:
    """create_app 里调用：每个数据集挂载 /mcp-bridge/<mount>/{uid}/mcp + /mcp（试连）。"""
    global _DATASET_BRIDGES
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp_bridge import _BearerGuardMiddleware

    _DATASET_BRIDGES = []
    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    for spec in SYSTEM_DATASETS:
        key, mount_key, title = spec["key"], spec["mount"], spec["title"]
        server = _make_dataset_server(key, title)
        manager = StreamableHTTPSessionManager(
            app=server,
            event_store=None,
            json_response=False,
            stateless=True,
        )
        sub = Starlette(routes=[
            Mount("/{uid:int}/mcp", app=_DatasetUidHandler(manager)),
            Mount("/mcp", app=_DatasetUidHandler(manager)),
        ])
        if token:
            sub.add_middleware(_BearerGuardMiddleware, token=token)
        else:
            logger.warning(f"[datasets] MCP_BRIDGE_TOKEN 未配置，/mcp-bridge/{mount_key} 端点暂无鉴权（仅适合本机开发）")
        app.mount(f"/mcp-bridge/{mount_key}", sub, name=f"mcp-bridge-{mount_key}")
        _DATASET_BRIDGES.append((key, server, manager))
        logger.info(f"[datasets] 已挂载 /mcp-bridge/{mount_key}/{{uid}}/mcp（{title}）")


async def start_dataset_bridges() -> None:
    """lifespan 启动期：拉起所有数据集桥的 session manager 任务组（AsyncExitStack 持有）。"""
    global _DATASET_STACK
    if not _DATASET_BRIDGES:
        return
    try:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        for key, _, manager in _DATASET_BRIDGES:
            await stack.enter_async_context(manager.run())
            logger.info(f"[datasets] session manager 就绪：{key}")
        _DATASET_STACK = stack
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[datasets] session manager 启动失败（数据集不可用，不影响主服务）: {e}")


async def stop_dataset_bridges() -> None:
    """lifespan 退出期：关闭所有数据集桥的 session manager。"""
    global _DATASET_STACK
    if _DATASET_STACK is not None:
        try:
            await _DATASET_STACK.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[datasets] session manager 关闭异常: {e}")
        _DATASET_STACK = None


# ── 默认启用（仅 BRAND_VARIANT=standard 行业版） ─────────────────────────────
#
# standard 变体部署自带标准库数据，三个系统数据集对新用户默认添加并启用：
#   - 启动时只幂等播种 agent_connector 登记行（新装库无需手工 SQL），不碰用户偏好；
#   - 新用户注册时（自助注册 / 短信建号）即时补默认偏好行；
#   - 存量用户的默认启用是一次性迁移动作，按需跑脚本调 ensure_dataset_prefs，
#     不做成启动期例行回填（用户没启用的不应被系统反复补上）。
# 只补「没有偏好行」的用户×数据集组合；用户已显式禁用/移除的偏好行一律尊重不覆盖。
# generic（通用版）无标准数据源，不做任何默认启用。


def _is_standard_variant() -> bool:
    from app.settings.config import settings

    return (settings.BRAND_VARIANT or "standard").lower() == "standard"


async def ensure_standard_dataset_rows() -> None:
    """幂等播种三个系统数据集的 agent_connector 登记行（已存在则跳过，不覆盖既有行）。"""
    from app.models.standard.agent import AgentConnector

    port = os.environ.get("APP_PORT", "9999")
    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    for spec in SYSTEM_DATASETS:
        existing = await AgentConnector.get_or_none(connector_key=spec["key"])
        if existing is not None:
            continue
        await AgentConnector.create(
            connector_key=spec["key"],
            name=spec["title"],
            description=spec.get("seed_desc") or "",
            transport="streamable_http",
            url=f"http://localhost:{port}/mcp-bridge/{spec['mount']}/mcp",
            kind="dataset",
            credential_mode="shared",
            api_key=token or None,
            is_enabled=1,
            user_id=None,
            min_tier_code=None,
        )
        logger.info(f"[datasets] 已播种系统数据集登记行：{spec['title']}（{spec['key']}）")


async def ensure_dataset_prefs(user_ids: List[int]) -> int:
    """为指定用户补齐三个系统数据集的默认偏好行（仅补缺失，不覆盖用户已有选择）。

    返回新建行数。非 standard 变体 / 空入参直接返回 0。
    消费方：注册钩子（新用户即时补）+ 存量用户一次性迁移脚本。
    """
    from app.models.standard.agent import AgentConnectorUserPref

    if not _is_standard_variant():
        return 0
    ids = [u for u in (user_ids or []) if u]
    if not ids:
        return 0
    keys = list(SYSTEM_DATASET_KEYS)
    created = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        have = set(
            await AgentConnectorUserPref.filter(user_id__in=chunk, connector_key__in=keys).values_list(
                "user_id", "connector_key"
            )
        )
        missing = [
            AgentConnectorUserPref(user_id=uid, connector_key=k, is_added=1, is_enabled=1)
            for uid in chunk
            for k in keys
            if (uid, k) not in have
        ]
        if missing:
            await AgentConnectorUserPref.bulk_create(missing, batch_size=300)
            created += len(missing)
    return created


__all__ = [
    "SYSTEM_DATASETS",
    "SYSTEM_DATASET_KEYS",
    "mount_dataset_bridges",
    "start_dataset_bridges",
    "stop_dataset_bridges",
    "ensure_standard_dataset_rows",
    "ensure_dataset_prefs",
]
