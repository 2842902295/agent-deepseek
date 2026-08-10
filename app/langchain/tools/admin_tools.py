"""
超管系统管理工具集（agent 用，仅 R_SUPER 可见）——通用工具版

设计思路（2026-08-08 重构，取代 17 个专用工具）：不为每个场景造专用工具，
用少数通用工具 + 提示词纪律 + 代码级红线覆盖全部管理诉求：

- 读侧全通用：admin_tables（可管理哪些表）/ admin_table_schema（字段结构）/
  admin_sql（只读 SQL，任意统计与排查——活跃用户、登录趋势、用量分析等
  固定工具没预置的问题都能查）
- 写侧通用入口：admin_save_record（建/改单条记录，可写字段由 ORM 模型自动推导：
  除主键与自动时间戳外全部放开，注册表内所有表均可写）、
  admin_delete_record（删单条记录，按表保护与级联）、
  admin_grant_role（RBAC 授权，M2M 增撤）

安全约定：
- 工厂闭包绑定操作者 user_id（超管本人）。所有写操作写审计日志
  （logs 表 LogType.AdminLog + api_logs 存操作详情），绕过 HTTP 中间件也有痕迹。
- 写操作全部带 user_confirmed 参数（两段确认）：
    False（默认）→ 不执行，返回 needConfirm=true + 操作预览，由主 agent 向用户
    复述并取得明确确认后，再次委派时传 True 执行。LLM 幻觉或越权时最多拿到预览。
- 硬红线（代码层拒绝，不依赖提示词）：禁止禁用 R_SUPER 用户或改其角色、禁止删除
  builtin 技能、禁止删除仍有子菜单的菜单、操作者不可禁用自己——防超管锁死与误删。
- admin_sql 只读安全门：仅单条 SELECT、写/危险关键字拒绝、表白名单、
  禁注释/多语句/系统库、password 列禁查、未写 LIMIT 自动补。
- 运行时权限复查（纵深防御）：每次工具调用前重查操作者是否仍为 R_SUPER——
  构建后超管被撤权时，进行中的流即使仍持有旧 agent 实例也无法再执行管理操作；
  复查失败（含 DB 异常）一律按无权限处理，宁紧勿松。
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Annotated, Any, Optional

from langchain.tools import tool

logger = logging.getLogger(__name__)


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _canon_enum(enum_cls, v) -> tuple:
    """把枚举输入归一成枚举名（如 'disable'）：接受 DB 值（'2'）或名称（'disable'）。

    CharEnumField 的坑：StatusType.enable 的 DB 值是 '1' 而非 'enable'，
    agent 两种写法都可能给。返回 (枚举名, 错误信息)。
    """
    try:
        return enum_cls(v).name, None
    except ValueError:
        pass
    if str(v) in enum_cls.__members__:
        return str(v), None
    choices = ", ".join(f"{m.name}={m.value}" for m in enum_cls)
    return None, f"值 {v!r} 无效，可选：{choices}"


def _need_confirm(preview: dict, note: str = "") -> str:
    """写操作未确认时的统一返回：只给预览，绝不执行。"""
    payload = {
        "ok": False,
        "needConfirm": True,
        "preview": preview,
        "note": note or "该操作尚未取得用户确认。请把 preview 完整转述给用户，待用户明确同意后，由主 agent 在任务描述中注明『用户已确认』并再次委派，届时以 user_confirmed=True 执行。",
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


async def _audit(operator_id: int, action: str, detail: dict, result: str, ok: bool) -> None:
    """审计落库：api_logs 存操作详情，logs 表挂 AdminLog 类型。failure-safe，不阻塞主流程。"""
    try:
        from app.core.ctx import CTX_X_REQUEST_ID
        from app.models.system.admin import APILog, Log
        from app.models.system.utils import LogType

        api_log = await APILog.create(
            x_request_id=CTX_X_REQUEST_ID.get() or "",
            request_domain="agent-runtime",
            request_path=f"system-admin/{action}",
            request_data=detail,
            response_data={"result": result},
            response_code="0000" if ok else "4000",
        )
        # 与 core/middlewares.py 同款写法：关系对象直接进 create
        await Log.create(log_type=LogType.AdminLog, by_user_id=operator_id, api_log=api_log)
    except Exception as e:  # 审计失败不影响操作本身，但要留告警
        logger.warning(f"[admin_tools] 审计写入失败 action={action}: {e}")


async def _reload_model_blocks_safe(src: str) -> None:
    """模型配置表（agent_model_block / agent_model_config）写后钩子：重载块定义 + 清 4 层缓存，立即生效。

    failure-safe：重载失败只告警（数据已落库，下次启动 / 切换仍会生效）。
    """
    try:
        from app.langchain.model_selection import reload_model_blocks

        await reload_model_blocks()
    except Exception as e:
        logger.warning(f"[admin_tools] 模型块热重载失败（{src}）：{e}")


async def _clear_role_agent_cache_safe(src: str) -> None:
    """agent_role_model_config 写后钩子：清按用户缓存的 agent 实例，角色配置立即生效。

    只清 agent 实例（块定义 / llm 单例没变，不必走完整 reload_model_blocks）。
    failure-safe：清理失败只告警（cache_key 含 profile，最迟下次配置变化也会重建）。
    """
    try:
        from app.langchain.role_model_profile import clear_agent_instances

        clear_agent_instances()
    except Exception as e:
        logger.warning(f"[admin_tools] 清 agent 实例缓存失败（{src}）：{e}")


# ── 表注册表（懒加载，避免模块导入期拉起整个 models 链）──────────────────────


def _load_registry() -> dict:
    """可管理表 → ORM 模型。读（admin_sql）与写（admin_save/delete）共用这份清单。"""
    from app.models.standard.agent import (
        AgentArtifact,
        AgentDailyBrief,
        AgentMessage,
        AgentProfession,
        AgentQuickAction,
        AgentQuickActionCategory,
        AgentQuickActionExample,
        AgentQuickActionLink,
        AgentSession,
        AgentSkill,
        AgentSkillFile,
        AgentUserSubscription,
        AgentWorkflow,
    )
    from app.models.standard.agent_task import AgentScheduledTask, AgentScheduledTaskRun
    from app.models.standard.model_config import AgentModelBlock, AgentModelConfig, AgentRoleModelConfig
    from app.models.system.admin import Api, APILog, Button, Log, Menu, Role, User

    return {
        "users": User,
        "roles": Role,
        "menus": Menu,
        "apis": Api,
        "buttons": Button,
        "logs": Log,
        "api_logs": APILog,
        "agent_session": AgentSession,
        "agent_message": AgentMessage,
        "agent_skill": AgentSkill,
        "agent_skill_file": AgentSkillFile,
        "agent_quick_action": AgentQuickAction,
        "agent_quick_action_category": AgentQuickActionCategory,
        "agent_quick_action_example": AgentQuickActionExample,
        "agent_quick_action_link": AgentQuickActionLink,
        "agent_scheduled_task": AgentScheduledTask,
        "agent_scheduled_task_run": AgentScheduledTaskRun,
        "agent_workflow": AgentWorkflow,
        "agent_artifact": AgentArtifact,
        "agent_profession": AgentProfession,
        "agent_user_subscription": AgentUserSubscription,
        "agent_daily_brief": AgentDailyBrief,
        "agent_model_block": AgentModelBlock,
        "agent_model_config": AgentModelConfig,
        "agent_role_model_config": AgentRoleModelConfig,
    }


# 伪字段：不是真实表列，但 admin_save_record 接受并有专门处理
# （users.role_codes = M2M 全量替换；menus.grant_to_role_codes = 仅新建时把菜单授权给角色）。
_PSEUDO_FIELDS: dict = {
    "users": ("role_codes",),
    "menus": ("grant_to_role_codes",),
}


def _writable_fields(tname: str, model) -> list:
    """可写字段 = 全部真实列（除主键、自动时间戳）+ 该表伪字段。

    注册表内所有表均可写（超管通用管理能力，2026-08-08 起不再维护按表白名单）；
    字段范围从 ORM 模型自动推导，新增表 / 加列无需改这里。
    FK 源列（如 api_log_id，fields_map 里查不到）同样放行，setattr 直写外键 id。
    """
    fields = []
    for fname in model._meta.fields_db_projection:
        f = model._meta.fields_map.get(fname)
        if f is not None and (getattr(f, "pk", False) or getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False)):
            continue
        fields.append(fname)
    return fields + list(_PSEUDO_FIELDS.get(tname, ()))


def _normalize_table(table: str) -> str:
    return (table or "").strip().strip("`").lower()


# ── admin_sql 安全门（与 db_tools 的 standard_query 同款思路，表范围不同）────


def _validate_admin_sql(sql: str, allowed_tables: set) -> Optional[str]:
    """只读 SQL 校验。返回拒绝原因，None 表示放行。"""
    from app.langchain.tools.db_tools import (
        _DENY_KEYWORD_RE,
        _SYSTEM_DB_TOKENS,
        _cte_names,
        _extract_tables,
    )

    s = (sql or "").strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        return "SQL 为空"
    if ";" in s:
        return "不允许多条语句，一次只能执行一条 SELECT"
    if "--" in s or "/*" in s or "#" in s:
        return "不允许 SQL 注释（--、/*、#）"
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        return "仅允许只读 SELECT 查询"
    m = _DENY_KEYWORD_RE.search(s)
    if m:
        return f"含禁用关键字 {m.group(1).upper()}，仅允许只读查询"
    low = s.lower()
    for bad in _SYSTEM_DB_TOKENS:
        if bad in low:
            return f"不允许访问系统库表：{bad.rstrip('.')}"
    if re.search(r"\bpassword\b", low):
        return "禁止查询 password 列（重置密码请用 admin_save_record，哈希值无查看意义）"
    if re.search(r"\bapi_key\b", low):
        return "禁止查询 api_key 列（模型凭据明文敏感；确认某块是否已配置只需看该列是否非空，不需要明文；新块要复用已有块的 key 用 admin_save_record 的 copy_api_key_from 参数）"
    ctes = _cte_names(s)
    bad_tables = sorted({t for t in _extract_tables(s) if t.lower() not in allowed_tables and t.lower() not in ctes})
    if bad_tables:
        return f"不允许查询的表：{', '.join(bad_tables)}（可管理表见 admin_tables）"
    return None


def make_admin_tools(user_id: Optional[int]):
    """按超管 user_id 创建系统管理工具集（6 个通用工具）。user_id 为 None 返回空列表。"""
    if user_id is None:
        return []

    operator_id = user_id  # 闭包固化操作者身份

    # ── 读侧：通用三件套 ────────────────────────────────────────────────────

    @tool
    async def admin_tables() -> str:
        """列出可管理的全部数据表（表名 + 说明 + 是否可经 admin_save_record/admin_delete_record 写入）。统计分析、排查问题前先来这里看有哪些数据。"""
        registry = _load_registry()
        rows = []
        for tname, model in registry.items():
            rows.append({
                "table": tname,
                "description": model._meta.table_description or model.__name__,
                "writable": True,  # 注册表内所有表均可写（字段自动推导，除主键/自动时间戳）
            })
        return _ok(rows)

    @tool
    async def admin_table_schema(table: Annotated[str, "表名（见 admin_tables）"]) -> str:
        """查看指定表的字段结构（字段名、类型、可否为空、中文说明）。写 admin_sql 前先用它确认字段名，不要凭猜测写。"""
        registry = _load_registry()
        model = registry.get(_normalize_table(table))
        if model is None:
            return _err(f"不可管理的表：{table}。可管理表：{sorted(registry)}")
        cols = []
        for fname, col in model._meta.fields_db_projection.items():
            f = model._meta.fields_map.get(fname)
            entry = {
                "column": col,
                "type": type(f).__name__ if f is not None else "?",
                "null": bool(getattr(f, "null", True)) if f is not None else True,
                "description": getattr(f, "description", "") or "",
            }
            enum_type = getattr(f, "enum_type", None)
            if enum_type is not None:
                # 库里存的是 value（如 '1'），不是名称——必须把映射给 agent 看
                entry["enum"] = {m.name: m.value for m in enum_type}
            cols.append(entry)
        return _ok({"table": model._meta.db_table, "columns": cols})

    @tool
    async def admin_sql(sql: Annotated[str, "要执行的只读 SELECT 语句（只能查 admin_tables 里的表）"]) -> str:
        """
        对系统管理相关表执行只读 SQL，返回 JSON 结果。用于任何统计 / 排查 / 分析：
        活跃用户数、登录趋势、会话量、用量排行、某用户的行为时间线等——自由组合
        GROUP BY / COUNT / WHERE 时间条件即可，不必等专用接口。

        限制（违反会直接拒绝）：仅单条 SELECT；禁写操作、注释、多语句；
        只能查 admin_tables 列出的表；password 列禁查；不写 LIMIT 自动追加 LIMIT 200。

        常用口径提示：users.last_login=最后登录时间；logs/api_logs 按 create_time
        记录操作与请求；agent_session/agent_message 记录会话与消息（create_time 可查时间）。
        """
        from tortoise import Tortoise

        from app.langchain.tools.db_tools import _QUERY_RESULT_CAP, _compact_row, _ensure_limit
        from app.settings.config import settings

        clean = (sql or "").strip().rstrip(";").strip()
        conn_alias = next(iter(settings.TORTOISE_ORM["connections"]))
        allowed = set(_load_registry().keys())
        reason = _validate_admin_sql(clean, allowed)
        if reason:
            logger.warning(f"[admin_sql] SQL 被拒：{reason} | {clean[:200]}")
            return _err(f"查询被拒绝：{reason}")
        clean = _ensure_limit(clean)
        try:
            conn = Tortoise.get_connection(conn_alias)
            rows = [_compact_row(dict(r)) for r in await conn.execute_query_dict(clean)]
        except Exception as e:
            return _err(f"SQL 执行失败：{e}")
        # SELECT * 兜底脱敏：凭据明文不进 agent 上下文（与 password 禁查同理）
        for r in rows:
            if isinstance(r, dict) and r.get("api_key"):
                r["api_key"] = "[已配置]"

        resp: dict = {"ok": True, "rows": rows, "count": len(rows)}
        total_size = len(json.dumps(rows, ensure_ascii=False, default=str))
        if total_size > _QUERY_RESULT_CAP:
            kept: list = []
            acc = 2
            for r in rows:
                piece = len(json.dumps(r, ensure_ascii=False, default=str)) + 1
                if acc + piece > _QUERY_RESULT_CAP and kept:
                    break
                kept.append(r)
                acc += piece
            resp["rows"] = kept
            resp["count"] = len(kept)
            resp["note"] = "结果过大已按行截断：聚合统计（GROUP BY + COUNT）通常比拉明细更合适。"
        return json.dumps(resp, ensure_ascii=False, default=str)

    # ── 写侧：通用入口 ──────────────────────────────────────────────────────

    async def _current_roles(u) -> list:
        # prefetch 之后属性是普通 list，未 prefetch 时是关系管理器，两种都要兼容
        rel = u.by_user_roles
        roles = rel if isinstance(rel, list) else await rel.all()
        return sorted(r.role_code for r in roles)

    @tool
    async def admin_save_record(
        table: Annotated[str, "表名（admin_tables 里 writable=true 的表）"],
        changes: Annotated[dict, "要改的字段 {字段名: 新值}，只改列出的字段；新建记录时给全必填字段"],
        record_id: Annotated[Optional[int], "记录 ID（先用 admin_sql 查到）；不传则新建"] = None,
        copy_api_key_from: Annotated[
            Optional[str],
            "仅 agent_model_block：传已有块的 block_key，后端直接拷贝它的 api_key（明文不进对话）；与已有块同厂商/同 key 加新模型时必须用这个，不要向用户重复要 key",
        ] = None,
        user_confirmed: Annotated[bool, "用户是否已明确确认本次操作（确认流程见你的系统提示）"] = False,
    ) -> str:
        """
        新建或更新一条记录（通用写入口）。高危操作，需用户确认。
        admin_tables 返回的所有表都可写（含快捷功能的分类/案例/关联等从表）。

        各表可改字段以 admin_table_schema 为准（主键与 create_time/update_time 不可改）；特殊字段：
        - users.password：新密码明文传入，服务端哈希后写入；users.role_codes：角色编码列表，**全量替换**
        - menus.grant_to_role_codes：仅新建时生效，建好后把菜单授权给这些角色
        - agent_scheduled_task.status 置 canceled：同时软删除并从调度器注销
        - agent_model_block：模型预设块定义（加模型 / 改模型配置）。chat 块 provider 留空、
          base_url/api_key/model 必填（OpenAI 兼容协议）；image/video 块 provider 必填且只能是
          已实现厂商；block_key 建好后不可改。api_key 回显一律脱敏；**复用已有块的 key（同厂商加新模型）
          走 copy_api_key_from 参数，后端直接拷贝，不要向用户要明文 key**
        - agent_model_config.selected_key：某类别当前选中块（切换模型一般用模型切换页，改前想清楚）
        - agent_role_model_config：按角色的模型配置（每角色一行）。块字段三态：null=跟随全局、
          块名=指定预设块、"DISABLED"=禁用（**仅限 image_block_key / video_block_key**，chat 不可禁用）。
          role_code 必须是 roles 表已有编码或保留兜底行 OTHER（OTHER 不属于任何真实角色，
          roles 表禁建同名角色）。一般用「按角色配置模型」页面操作，改前想清楚
        """
        from app.utils.security import get_password_hash

        tname = _normalize_table(table)
        registry = _load_registry()
        model = registry.get(tname)
        if model is None:
            return _err(f"不可管理的表：{table}。可管理表：{sorted(registry)}")
        if copy_api_key_from and tname != "agent_model_block":
            return _err("copy_api_key_from 仅可用于 agent_model_block 表")
        allowed_fields = _writable_fields(tname, model)
        if not isinstance(changes, dict) or not changes:
            return _err("changes 不能为空，格式 {字段名: 新值}")
        bad_keys = sorted(set(changes) - set(allowed_fields))
        if bad_keys:
            return _err(f"表 {tname} 不允许修改字段：{bad_keys}（允许：{allowed_fields}）")

        # ── 枚举归一（通用：任何枚举字段都处理；必须在红线校验前：否则传 DB 值 '2' 会绕过"禁用"判定）──
        for k in list(changes):
            f = model._meta.fields_map.get(k)
            enum_type = getattr(f, "enum_type", None) if f is not None else None
            if enum_type is not None:
                name, emsg = _canon_enum(enum_type, changes[k])
                if emsg:
                    return _err(f"{k} {emsg}")
                changes[k] = name
        # changes 内部的列表字段兜底：模型可能把 ["R_USER"] 传成 "R_USER,R_SUPER" 字符串
        from app.langchain.tools._tool_args import split_str_list

        for k in ("role_codes", "grant_to_role_codes", "allowed_role_codes"):
            if k in changes and not isinstance(changes[k], list):
                changes[k] = split_str_list(changes[k]) or []

        if record_id is not None:
            rec = await model.get_or_none(id=record_id)
            if rec is None:
                return _err(f"{tname} 记录不存在：id={record_id}")
        else:
            rec = None

        # ── 硬红线与业务校验 ──
        if tname == "users" and rec is not None:
            cur_roles = await _current_roles(rec)
            if "R_SUPER" in cur_roles and (changes.get("status_type") == "disable" or "role_codes" in changes):
                return _err("拒绝：R_SUPER 超管账号不允许禁用或变更角色（防止超管锁死）")
            if rec.id == operator_id and changes.get("status_type") == "disable":
                return _err("拒绝：不能禁用当前操作者自己的账号")
        if tname == "menus" and rec is None:
            missing = [k for k in ("menu_name", "menu_type", "route_name", "route_path") if not changes.get(k)]
            if missing:
                return _err(f"新建菜单缺少必填字段：{missing}")
            if changes.get("menu_type") not in ("catalog", "menu"):
                return _err("menu_type 只能是 catalog / menu")
            if await model.filter(route_name=changes["route_name"]).exists() or await model.filter(route_path=changes["route_path"]).exists():
                return _err(f"route_name 或 route_path 已存在（{changes['route_name']} / {changes['route_path']}），请先用 admin_sql 核对")
        if tname == "agent_model_block":
            from app.langchain.model_selection import validate_block_changes

            # 字段归一：category/provider 小写存储；provider 空串 → None；chat 块 provider 必须空
            if "category" in changes and changes["category"]:
                changes["category"] = str(changes["category"]).strip().lower()
            if "provider" in changes:
                pv = changes["provider"]
                changes["provider"] = str(pv).strip().lower() if pv else None
            if rec is not None and "block_key" in changes and str(changes["block_key"]).strip().upper() != rec.block_key:
                return _err("block_key 是块的身份标识（agent_model_config.selected_key 引用它），不可修改；需更换请删除后新建")
            if rec is None:
                bk = str(changes.get("block_key") or "").strip().upper()
                if not re.fullmatch(r"(CHAT|IMAGE|VIDEO)(_[A-Z0-9]+)?", bk):
                    return _err("block_key 须以 CHAT/IMAGE/VIDEO 开头，仅大写字母/数字/下划线，如 CHAT_DEEPSEEK")
                if await model.filter(block_key=bk).exists():
                    return _err(f"block_key {bk} 已存在（含软删除记录也不复用），请先用 admin_sql 核对")
                changes["block_key"] = bk
            # 复用已有块的 api_key：后端直接拷贝，明文不进对话/审计（须在合并校验前，拷来的 key 参与必填校验）
            if copy_api_key_from:
                if changes.get("api_key"):
                    return _err("api_key 与 copy_api_key_from 二选一，不能同时传")
                src_key = str(copy_api_key_from).strip().upper()
                src = await model.get_or_none(block_key=src_key, is_deleted=0)
                if src is None or not getattr(src, "api_key", None):
                    return _err(f"无法复用 api_key：块 {src_key} 不存在或未配置 api_key")
                changes["api_key"] = src.api_key
            # 必填与 provider 白名单：按「存量值 + 本次 changes」合并后的最终值校验
            merged = {f: getattr(rec, f, None) for f in ("category", "provider", "base_url", "api_key", "model")} if rec is not None else {}
            merged.update({k: v for k, v in changes.items() if k in ("category", "provider", "base_url", "api_key", "model")})
            emsg = validate_block_changes(str(merged.get("category") or ""), merged)
            if emsg:
                return _err(emsg)

        # ── 预览（敏感字段脱敏：password 哈希、api_key 凭据明文不进预览 / 审计 / 对话）──
        before: dict = {}
        if rec is not None:
            for k in changes:
                if k == "role_codes":
                    before["role_codes"] = await _current_roles(rec)
                elif k == "password":
                    before["password"] = "[已哈希]"
                elif k == "api_key":
                    before["api_key"] = "[已配置]" if getattr(rec, "api_key", None) else "[未配置]"
                else:
                    bv = getattr(rec, k, None)
                    before[k] = bv.name if isinstance(bv, Enum) else bv
        api_mark = f"[已设置，复用自 {str(copy_api_key_from).strip().upper()}]" if copy_api_key_from else "[已设置]"
        preview_changes = {k: ("[将哈希存储]" if k == "password" else api_mark if k == "api_key" else v) for k, v in changes.items()}
        preview: dict = {
            "table": tname,
            "mode": "update" if rec is not None else "create",
            **({"recordId": rec.id} if rec is not None else {}),
            **({"before": before} if before else {}),
            "changes": preview_changes,
        }
        if not user_confirmed:
            return _need_confirm(preview)

        # ── 执行 ──
        try:
            if rec is None:
                rec = model()
            special = {}
            for k, v in changes.items():
                if k == "password":
                    rec.password = get_password_hash(password=str(v))
                elif k in ("role_codes", "grant_to_role_codes"):
                    special[k] = v
                else:
                    f = model._meta.fields_map.get(k)
                    enum_type = getattr(f, "enum_type", None) if f is not None else None
                    if enum_type is not None:
                        setattr(rec, k, enum_type[v])  # 已归一为枚举名；CharEnumField 需显式转枚举
                    else:
                        setattr(rec, k, v)
            await rec.save()

            if tname == "users" and "role_codes" in special:
                from app.models.system.admin import Role

                want = sorted(set(special["role_codes"]))
                exist = {r.role_code: r for r in await Role.filter(role_code__in=want)}
                missing = [c for c in want if c not in exist]
                if missing:
                    return _err(f"角色不存在：{missing}（可用 admin_sql 查 roles 表）")
                # Tortoise M2M 无 set()，全量替换 = clear + add
                await rec.by_user_roles.clear()
                if exist:
                    await rec.by_user_roles.add(*exist.values())

            granted: list = []
            if tname == "menus" and "grant_to_role_codes" in special and special["grant_to_role_codes"]:
                from app.models.system.admin import Role

                roles = await Role.filter(role_code__in=special["grant_to_role_codes"])
                for r in roles:
                    await r.by_role_menus.add(rec)
                    granted.append(r.role_code)

            if tname == "agent_scheduled_task" and changes.get("status") == "canceled":
                rec.is_deleted = 1
                await rec.save()
                try:
                    from app.core.scheduler import unregister_scheduled_task

                    unregister_scheduled_task(rec)
                except Exception as e:
                    logger.warning(f"[admin_tools] 注销调度失败 task_id={rec.id}: {e}")
        except Exception as e:
            return _err(f"保存失败：{e}")

        # 模型配置表写后钩子：重载块定义 + 清 4 层缓存，改动立即全局生效
        if tname in ("agent_model_block", "agent_model_config"):
            await _reload_model_blocks_safe(f"save:{tname}")
        # 按角色模型配置写后：只需清 agent 实例缓存（cache_key 含 profile，重建即生效）
        if tname == "agent_role_model_config":
            await _clear_role_agent_cache_safe(f"save:{tname}")

        await _audit(operator_id, f"save_record:{tname}", preview, f"id={rec.id} granted={granted if tname == 'menus' else '-'}", True)
        return _ok({"table": tname, "mode": preview["mode"], "recordId": rec.id, "changes": preview_changes})

    @tool
    async def admin_delete_record(
        table: Annotated[str, "表名"],
        record_id: Annotated[int, "记录 ID（先用 admin_sql 查到）"],
        user_confirmed: Annotated[bool, "用户是否已明确确认本次操作"] = False,
    ) -> str:
        """删除一条记录（通用删除入口）。高危操作，需用户确认。内置保护：builtin 技能不可删；仍有子菜单的菜单不可删；快捷功能/技能会级联删除其从属记录。"""
        tname = _normalize_table(table)
        if tname == "users":
            return _err("拒绝：用户不可直接删除（历史数据关联多），请用 admin_save_record 将 status_type 置为 disable")
        if tname == "roles":
            return _err("拒绝：角色不可直接删除，请先移除角色下的用户并撤销其菜单/API 授权")
        registry = _load_registry()
        model = registry.get(tname)
        if model is None:
            return _err(f"不可管理的表：{tname}。可管理表：{sorted(registry)}")

        rec = await model.get_or_none(id=record_id)
        if rec is None:
            return _err(f"{tname} 记录不存在：id={record_id}")

        # ── 硬红线 ──
        if tname == "agent_skill" and getattr(rec, "source", None) == "builtin":
            return _err("拒绝：builtin 内置技能随系统分发，不可经对话删除")
        if tname == "menus":
            children = await model.filter(parent_id=record_id).count()
            if children:
                return _err(f"拒绝：菜单 {getattr(rec, 'menu_name', record_id)} 下还有 {children} 个子菜单，请先处理子菜单")

        # 级联信息（预览与执行一致）
        cascade: dict = {}
        if tname == "agent_quick_action":
            from app.models.standard.agent import AgentQuickActionExample, AgentQuickActionLink

            cascade["examples"] = await AgentQuickActionExample.filter(action_id=record_id).count()
            cascade["links"] = await AgentQuickActionLink.filter(action_id=record_id).count()
        elif tname == "agent_skill":
            from app.models.standard.agent import AgentSkillFile

            cascade["files"] = await AgentSkillFile.filter(skill_key=rec.skill_key).count()

        preview = {
            "table": tname,
            "recordId": record_id,
            "target": {k: getattr(rec, k, None) for k in ("name", "menu_name", "skill_key", "title", "task_key", "user_name") if hasattr(rec, k)},
            **({"cascade": cascade} if cascade else {}),
        }
        if tname == "agent_model_block":
            preview["target"] = {"block_key": rec.block_key, "label": rec.label, "category": rec.category}
            from app.models.standard import AgentModelConfig

            sel = await AgentModelConfig.filter(category=rec.category, selected_key=rec.block_key).first()
            if sel is not None:
                preview["note"] = f"该块是 {rec.category} 类别当前选中的模型，删除后系统将自动回退默认块"
        if not user_confirmed:
            return _need_confirm(preview)

        try:
            if tname == "agent_quick_action":
                from app.models.standard.agent import AgentQuickActionExample, AgentQuickActionLink

                await AgentQuickActionExample.filter(action_id=record_id).delete()
                await AgentQuickActionLink.filter(action_id=record_id).delete()
            elif tname == "agent_skill":
                from app.models.standard.agent import AgentSkillFile

                await AgentSkillFile.filter(skill_key=rec.skill_key).delete()
            if tname == "agent_model_block":
                # 软删除：保留墓碑（启动播种会跳过库里已有的 block_key，含软删），防止块从播种基线复活
                rec.is_deleted = 1
                await rec.save(update_fields=["is_deleted", "update_time"])
            else:
                await rec.delete()
        except Exception as e:
            return _err(f"删除失败：{e}")

        # 模型配置表写后钩子：重载块定义 + 清缓存（选中块被删时自动回退默认块）
        if tname in ("agent_model_block", "agent_model_config"):
            await _reload_model_blocks_safe(f"delete:{tname}")
        # 按角色模型配置行被删 = 该角色回退跟随全局：清 agent 实例缓存即生效
        if tname == "agent_role_model_config":
            await _clear_role_agent_cache_safe(f"delete:{tname}")

        await _audit(operator_id, f"delete_record:{tname}", preview, "done", True)
        return _ok({"table": tname, "deletedRecordId": record_id, **({"cascade": cascade} if cascade else {})})

    @tool
    async def admin_grant_role(
        role_code: Annotated[str, "角色编码，如 R_USER / R_SUPER"],
        grant_menu_ids: Annotated[Optional[list[int]], "新增授权的菜单 ID 列表"] = None,
        revoke_menu_ids: Annotated[Optional[list[int]], "撤销授权的菜单 ID 列表"] = None,
        grant_api_ids: Annotated[Optional[list[int]], "新增授权的 API ID 列表"] = None,
        revoke_api_ids: Annotated[Optional[list[int]], "撤销授权的 API ID 列表"] = None,
        user_confirmed: Annotated[bool, "用户是否已明确确认本次操作"] = False,
    ) -> str:
        """给角色增加 / 撤销 菜单与 API 授权（RBAC）。高危操作，需用户确认。ID 可用 admin_sql 查 menus / apis 表获得。"""
        from app.models.system.admin import Api, Menu, Role

        role = await Role.get_or_none(role_code=role_code)
        if role is None:
            return _err(f"角色不存在：{role_code}")
        if not any([grant_menu_ids, revoke_menu_ids, grant_api_ids, revoke_api_ids]):
            return _err("至少指定一项：grant_menu_ids / revoke_menu_ids / grant_api_ids / revoke_api_ids")

        # 校验 ID 都存在，并把名字带进预览（{id,name} 列表：审计 JSON 不接受 int 键）
        async def _names(model, ids, name_attr):
            rows = await model.filter(id__in=ids)
            have = {r.id: getattr(r, name_attr) for r in rows}
            missing = [i for i in ids if i not in have]
            found = [{"id": i, "name": have[i]} for i in ids if i in have]
            return found, missing

        preview: dict = {"role": role_code, "changes": {}}
        for key, mdl, name_attr, ids in [
            ("grantMenus", Menu, "menu_name", grant_menu_ids),
            ("revokeMenus", Menu, "menu_name", revoke_menu_ids),
            ("grantApis", Api, "api_path", grant_api_ids),
            ("revokeApis", Api, "api_path", revoke_api_ids),
        ]:
            if ids:
                found, missing = await _names(mdl, ids, name_attr)
                if missing:
                    return _err(f"{key} 中 ID 不存在：{missing}")
                preview["changes"][key] = found

        if not user_confirmed:
            return _need_confirm(preview)

        if grant_menu_ids:
            await role.by_role_menus.add(*await Menu.filter(id__in=grant_menu_ids))
        if revoke_menu_ids:
            await role.by_role_menus.remove(*await Menu.filter(id__in=revoke_menu_ids))
        if grant_api_ids:
            await role.by_role_apis.add(*await Api.filter(id__in=grant_api_ids))
        if revoke_api_ids:
            await role.by_role_apis.remove(*await Api.filter(id__in=revoke_api_ids))
        await _audit(operator_id, "grant_role", preview, "done", True)
        return _ok({"role": role_code, "changes": preview["changes"]})

    _all_tools = [
        admin_tables,
        admin_table_schema,
        admin_sql,
        admin_save_record,
        admin_delete_record,
        admin_grant_role,
    ]

    # ── 运行时权限复查（纵深防御）────────────────────────────────────────────
    # 工具不信任"构建期快照"：构建后超管被撤权时，qa.py 的缓存键分段会让后续请求
    # 重建 agent，但**进行中的流**可能仍持有旧实例。每次调用前重查操作者角色，
    # 已不是 R_SUPER 直接拒绝——管理操作低频，一次索引查询的开销可忽略。
    async def _still_super() -> bool:
        from app.models.system.admin import User

        u = await User.get_or_none(id=operator_id).prefetch_related("by_user_roles")
        return bool(u and any(r.role_code == "R_SUPER" for r in u.by_user_roles))

    from langchain_core.tools import StructuredTool as _StructuredTool

    from app.langchain.tools._tool_args import lenient_args_schema

    def _guarded(t):
        orig_coro = t.coroutine

        async def _wrap(**kwargs):
            try:
                ok = await _still_super()
            except Exception as e:
                logger.warning(f"[admin_tools] 权限复查失败：{e}")
                ok = False  # 查不了就按无权限处理，宁紧勿松
            if not ok:
                return _err("拒绝访问：操作者当前不是 R_SUPER（权限复查未通过）。角色可能已被调整，请重新进入会话。")
            return await orig_coro(**kwargs)

        return _StructuredTool.from_function(
            coroutine=_wrap,
            name=t.name,
            description=t.description,
            # 兼容模型把数组/对象参数传成 JSON 字符串（如 grant_menu_ids="[1,2]"）
            args_schema=lenient_args_schema(t.args_schema),
        )

    return [_guarded(t) for t in _all_tools]
