"""
过程时间线工具入参脱敏

SSE「过程时间线」把 tool_call 入参原样发给前端展示（折叠行摘要 + 展开参数区），
原始入参里的 SQL 语句、shell 命令、代码内容属于实现细节，与「回复中禁止暴露
技术实现细节」的铁律矛盾（系统提示只约束了模型回复，约束不到 UI 渲染）。

本模块在 qa.py / standard_evaluation.py 发出 SSE、落库过程项之前把原始 args
统一转换成「展示安全」的 dict：
  - 技术型工具（SQL 查询 / 命令执行 / 文件读写 / 系统管理等）：整体替换为中性
    业务描述，原始语句绝不外泄；
  - 业务型工具（标准检索 / 章节阅读 / 联网搜索等）：保留业务值（标准号、关键词、
    章节号等），键换成中文，丢弃纯技术参数（top_k / min_score 等）；
  - 未映射工具：通用兜底——长文本/代码态字符串与复杂结构替换为占位文案。

前端（process-timeline.vue）无需改动，渲染脱敏后的 dict 即可。
注意：subagent 委派类工具必须保留 `description` 键（前端 subagentDesc 取它做标题）。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from app.services.agent_runtime.tool_display_names import strip_tool_prefix

_PLACEHOLDER = "（技术细节已省略）"
_MAX_VAL = 80  # 展示值统一截断长度


def _t(v: Any, n: int = _MAX_VAL) -> str:
    s = str(v or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _basename(p: Any) -> str:
    s = str(p or "").strip().replace("\\", "/")
    return s.rsplit("/", 1)[-1] if s else ""


# ── SQL 意图启发式：不展示语句本身，只翻译成业务动作 ─────────────────────────


def _summarize_sql(sql: str) -> str:
    s = (sql or "").upper()
    if "COUNT(" in s:
        return "统计数据量"
    if "GROUP BY" in s:
        return "分组统计"
    if "DISTINCT" in s:
        return "去重统计"
    if "JOIN" in s:
        return "跨表关联查询"
    return "按条件查询"


# ── 逐工具规则（基础名 → 处理器） ─────────────────────────────────────────────


def _q_sql_query(a: dict) -> dict:
    return {"查询方式": _summarize_sql(str(a.get("sql") or ""))}


def _q_shell(a: dict) -> dict:
    return {"操作": "执行系统命令"}


def _q_schema(_a: dict) -> dict:
    return {"操作": "查看数据表结构"}


def _q_tables(_a: dict) -> dict:
    return {"操作": "列出数据表"}


def _q_admin_save(_a: dict) -> dict:
    return {"操作": "保存系统记录"}


def _q_admin_delete(_a: dict) -> dict:
    return {"操作": "删除系统记录"}


def _q_admin_grant(_a: dict) -> dict:
    return {"操作": "角色授权"}


def _q_file_write(a: dict) -> dict:
    fn = _basename(a.get("file_path") or a.get("path"))
    return {"文件": _t(fn)} if fn else {"操作": "写入文件"}


def _q_file_read(a: dict) -> dict:
    fn = _basename(a.get("file_path") or a.get("path"))
    return {"文件": _t(fn)} if fn else {"操作": "读取文件"}


def _q_find(a: dict) -> dict:
    pat = a.get("pattern") or a.get("path") or ""
    return {"范围": _t(pat)} if pat else {}


def _q_grep(a: dict) -> dict:
    pat = a.get("pattern") or ""
    return {"内容特征": _t(pat)} if pat else {}


def _q_todo(_a: dict) -> dict:
    return {"操作": "更新任务清单"}


def _q_subagent(a: dict) -> dict:
    # description 是业务化标题（前端 subagentDesc 也用它），保留；prompt 隐藏
    desc = a.get("description") or ""
    return {"description": _t(desc)} if desc else {}


def _q_skill(a: dict) -> dict:
    name = a.get("skill") or a.get("name") or ""
    return {"技能": _t(name)} if name else {}


def _q_chapters(a: dict) -> dict:
    out: Dict[str, str] = {}
    if a.get("standard_no"):
        out["标准号"] = _t(a["standard_no"])
    if a.get("title_no_prefix"):
        out["章节"] = _t(a["title_no_prefix"])
    if a.get("near_title_no"):
        out["起始章节"] = _t(a["near_title_no"])
    if a.get("keyword"):
        out["关键词"] = _t(a["keyword"])
    if a.get("toc_only"):
        out["模式"] = "仅看目录"
    return out or {"操作": "读取正文章节"}


def _q_search_standards(a: dict) -> dict:
    out: Dict[str, str] = {}
    if a.get("standard_no"):
        out["标准号"] = _t(a["standard_no"])
    if a.get("keyword"):
        out["关键词"] = _t(a["keyword"])
    if a.get("state"):
        out["状态"] = _t(a["state"])
    return out or {"范围": "全库浏览"}


def _q_search_terms(a: dict) -> dict:
    out: Dict[str, str] = {}
    if a.get("standard_no"):
        out["标准号"] = _t(a["standard_no"])
    if a.get("keyword"):
        out["关键词"] = _t(a["keyword"])
    return out or {"范围": "全库浏览"}


def _q_vec_search(a: dict) -> dict:
    out: Dict[str, str] = {}
    if a.get("query"):
        out["检索内容"] = _t(a["query"])
    if a.get("scope_standard_nos"):
        out["限定标准"] = _t(a["scope_standard_nos"])
    return out or {"操作": "语义检索"}


def _q_web_search(a: dict) -> dict:
    q = a.get("query") or a.get("keywords") or ""
    return {"搜索": _t(q)} if q else {}


def _q_fetch(a: dict) -> dict:
    url = str(a.get("url") or "")
    return {"页面": _t(url)} if url else {}


def _q_vision(a: dict) -> dict:
    q = a.get("question") or a.get("prompt") or ""
    return {"看图问题": _t(q)} if q else {}


def _q_register_artifact(a: dict) -> dict:
    name = a.get("name") or ""
    return {"产物": _t(name)} if name else {}


def _q_chart(_a: dict) -> dict:
    return {"操作": "生成图表"}


def _q_scheduled_task(a: dict) -> dict:
    prompt = a.get("prompt") or a.get("task") or ""
    return {"任务": _t(prompt)} if prompt else {"操作": "创建定时任务"}


def _q_kb_search(a: dict) -> dict:
    q = a.get("query") or a.get("question") or ""
    return {"检索内容": _t(q)} if q else {}


def _q_kb_write(a: dict) -> dict:
    title = a.get("title") or a.get("name") or ""
    return {"条目": _t(title)} if title else {"操作": "更新知识库"}


def _q_memory(a: dict) -> dict:
    q = a.get("query") or a.get("content") or ""
    return {"内容": _t(q)} if q else {}


def _q_standard_no_only(a: dict) -> dict:
    out: Dict[str, str] = {}
    if a.get("standard_no"):
        out["标准号"] = _t(a["standard_no"])
    if a.get("keyword") or a.get("keywords"):
        out["关键词"] = _t(a.get("keyword") or a.get("keywords"))
    return out


_TOOL_RULES: Dict[str, Callable[[dict], dict]] = {
    # 技术型：原始语句一律不出后端
    "standard_query": _q_sql_query,
    "admin_sql": _q_sql_query,
    "bash": _q_shell,
    "execute": _q_shell,
    "run_command": _q_shell,
    "standard_schema": _q_schema,
    "admin_table_schema": _q_schema,
    "standard_tables": _q_tables,
    "admin_tables": _q_tables,
    "admin_save_record": _q_admin_save,
    "admin_delete_record": _q_admin_delete,
    "admin_grant_role": _q_admin_grant,
    "write": _q_file_write,
    "write_file": _q_file_write,
    "edit": _q_file_write,
    "edit_file": _q_file_write,
    "read": _q_file_read,
    "read_file": _q_file_read,
    "glob": _q_find,
    "grep": _q_grep,
    "todo_write": _q_todo,
    "write_todos": _q_todo,
    "skill": _q_skill,
    "create_chart": _q_chart,
    # 子代理委派：保留 description（前端标题用），隐藏 prompt
    "task": _q_subagent,
    "subagent": _q_subagent,
    "quality-scout": _q_subagent,
    "skill-management": _q_subagent,
    "knowledge-base": _q_subagent,
    "chat-history": _q_subagent,
    "system-admin": _q_subagent,
    # 业务型：保留业务值，键换成中文
    "get_standard_chapters": _q_chapters,
    "get_near_chapters": _q_chapters,
    "search_standards": _q_search_standards,
    "search_terms": _q_search_terms,
    "vector_search_standards_ob": _q_vec_search,
    "vector_search_chapters": _q_vec_search,
    "search_terms_vector": _q_vec_search,
    "vector_hybrid_search": _q_vec_search,
    "get_cached_indicators": _q_standard_no_only,
    "search_candidate_standards": _q_standard_no_only,
    "web_search": _q_web_search,
    "tavily_search": _q_web_search,
    "brave_web_search": _q_web_search,
    "brave_local_search": _q_web_search,
    "bailian_web_search": _q_web_search,
    "fetch": _q_fetch,
    "vision_inspect": _q_vision,
    "register_artifact": _q_register_artifact,
    "create_scheduled_task": _q_scheduled_task,
    "kb_search": _q_kb_search,
    "kb_query_collection": _q_kb_search,
    "kb_create": _q_kb_write,
    "kb_update": _q_kb_write,
    "kb_add_documents": _q_kb_write,
    "manage_memory": _q_memory,
    "search_memory": _q_memory,
}

# 兜底：疑似代码/语句特征的字符串（未映射工具也拦得住）
_CODE_HINT_RE = re.compile(
    r"(\bselect\b[\s\S]{0,200}\bfrom\b|\b(insert|update|delete|drop|alter)\b\s+|"
    r"\bdef\s+\w+\s*\(|\bimport\s+[\w.]+|\bfunction\s+\w+\s*\(|#!/|\bgit\s+\w+)",
    re.IGNORECASE,
)


def _generic_redact(args: dict) -> dict:
    out: Dict[str, Any] = {}
    for k, v in args.items():
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str):
            if len(v) > 120 or _CODE_HINT_RE.search(v):
                out[k] = _PLACEHOLDER
            else:
                out[k] = v
        else:
            out[k] = _PLACEHOLDER
    return out


def sanitize_tool_args(tool_name: str, args: Optional[dict]) -> dict:
    """
    把工具原始入参转换成时间线展示安全的 dict。

    返回值只用于展示（SSE process 事件 + 落库过程项），绝不能回流给模型。
    """
    if not isinstance(args, dict) or not args:
        return {}
    rule = _TOOL_RULES.get(strip_tool_prefix(tool_name or ""))
    if rule is not None:
        try:
            out = rule(args)
            return out if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return _generic_redact(args)


# ── 工具返回值脱敏 ────────────────────────────────────────────────────────────
#
# 口径（对普通用户；超管/管理员保留原始返回便于排查）：
# - 技能加载/保存类：SKILL.md 全文是提示词内部资产，**绝不能**出现在普通用户时间线，
#   整体替换为一句中性状态文案；
# - 技术型工具（命令执行 / 文件读写 / 表结构 / 系统管理等）：返回多为代码、路径、
#   原始记录，替换为中性动作文案；
# - 业务型工具（标准查询 / 正文阅读 / 术语 / 语义检索 / 联网 / 知识库等）：返回就是
#   用户要看的业务数据，原样保留；
# - 未映射工具（用户自接的外部连接器等）：数据属于用户自己接的服务，原样保留。

_RESULT_FULL_HIDE: Dict[str, str] = {
    # 技能细节绝不暴露：加载/保存/删除/安装的返回都含 SKILL.md 内容或内部字段
    "skill": "已加载技能规范",
    "skill_read": "已读取技能",
    "skill_save": "技能已保存",
    "skill_delete": "技能已删除",
    "skill_install": "技能已安装",
}

_RESULT_NEUTRAL: Dict[str, str] = {
    "bash": "命令已执行",
    "execute": "命令已执行",
    "run_command": "命令已执行",
    "read": "文件已读取",
    "read_file": "文件已读取",
    "write": "文件已写入",
    "write_file": "文件已写入",
    "edit": "文件已编辑",
    "edit_file": "文件已编辑",
    "glob": "已完成文件查找",
    "grep": "已完成内容检索",
    "todo_write": "任务清单已更新",
    "write_todos": "任务清单已更新",
    "standard_tables": "已查看数据表清单",
    "standard_schema": "已查看表结构",
    "admin_tables": "已查看可管理表",
    "admin_table_schema": "已查看表结构",
    "admin_sql": "已完成系统数据查询",
    "admin_save_record": "系统记录已保存",
    "admin_delete_record": "系统记录已删除",
    "admin_grant_role": "已完成授权",
    "create_chart": "图表已生成",
    "register_artifact": "产物已登记",
    "vector_lib_list": "已列出向量库",
    "vector_lib_search": "已完成向量库检索",
    "vector_lib_create": "向量库已创建",
    "vector_lib_add_items": "向量库条目已写入",
    "vector_lib_delete_items": "向量库条目已删除",
}


def sanitize_tool_result(tool_name: str, content: Any, is_error: bool = False) -> str:
    """
    把工具返回内容转换成时间线展示安全的文案（仅普通用户走此路径）。

    业务数据类返回原样保留；技能加载类与技术型工具替换为中性文案。
    返回值只用于展示，绝不能回流给模型。
    """
    base = strip_tool_prefix(tool_name or "")
    if is_error:
        if base in _RESULT_FULL_HIDE or base in _RESULT_NEUTRAL:
            return "操作未成功"
        return str(content or "")
    hidden = _RESULT_FULL_HIDE.get(base)
    if hidden is not None:
        return hidden
    neutral = _RESULT_NEUTRAL.get(base)
    if neutral is not None:
        return neutral
    return str(content or "")


__all__ = ["sanitize_tool_args", "sanitize_tool_result"]
