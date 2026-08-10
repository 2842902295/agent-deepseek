"""
对话历史检索工具集（agent 用）

让 qa agent 在用户问"我们之前聊过 xxx 吗 / 什么时候聊的 xx"时，能够自主跨会话回溯
当前用户名下的全部历史聊天记录，按关键词 / 标题 / 时间窗口定位具体会话和消息。

工具列表：
- search_chat_history:    按关键词跨会话搜消息（user / assistant 双方）
- list_recent_sessions:   列出最近会话（按 update_time 倒序，可按标题过滤）
- get_session_messages:   读取指定会话的完整消息时间线（单条截断 2000 字符）
- get_message_detail:     按消息 ID 读取单条消息全文（不截断）

工厂闭包注入 user_id；可选注入当前 session_key（agent 按用户缓存时不绑定会话，
留空即不做当前会话排除，由主 agent 委派时在任务描述里说明需排除的会话）。

注意：与 langmem 的 manage_memory / search_memory 不同——那是 agent 主动归档下来的"事实"，
本工具直接打 agent_message 表的 content 列，看到的是原始对话流水。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Optional

from langchain.tools import tool

from app.models.standard.agent import AgentMessage, AgentSession


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _excerpt(content: str, keyword: str, radius: int = 60) -> str:
    """在 content 里截取关键词附近 radius 字符范围；找不到时截取开头一段。"""
    if not content:
        return ""
    idx = content.lower().find(keyword.lower()) if keyword else -1
    if idx < 0:
        head = content[: radius * 2].strip()
        return head + ("…" if len(content) > len(head) else "")
    start = max(0, idx - radius)
    end = min(len(content), idx + len(keyword) + radius)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


def make_history_tools(user_id: Optional[int], current_session_key: Optional[str] = None):
    """按 user_id + 当前 session_key 创建一组对话历史回溯工具。

    Args:
        user_id: 当前用户 ID；为 None 时返回空列表（未登录态不暴露任何检索能力）
        current_session_key: 当前会话 key；不为 None 时，默认从结果里排除当前会话
            （用户在 X 会话里问"以前聊过吗"，意图通常不含 X 自己）

    Returns:
        list[Tool]：4 个 langchain tool，可直接拼到 agent 的 tools 列表
    """
    if user_id is None:
        return []

    @tool
    async def search_chat_history(
        query: Annotated[
            str,
            "关键词（必填，建议用用户原话里的实词，如『OceanBase 索引』『GraphQL』）。"
            "会做大小写不敏感的 LIKE 子串匹配，对中英文都生效。",
        ],
        top_k: Annotated[int, "返回最多 N 条命中消息，默认 10，最大 30"] = 10,
        days: Annotated[
            Optional[int],
            "时间窗口（天）。只搜最近 N 天内的消息；不传则不限时间，全部历史都搜。",
        ] = None,
        include_current_session: Annotated[
            bool,
            "是否包含当前会话。默认 False——用户问『之前聊过吗』时通常想要别的会话。",
        ] = False,
    ) -> str:
        """
        跨会话检索当前用户的历史聊天消息原文（仅本人未删会话，user 与 assistant 双方）。

        用户问"我们什么时候聊过 xx / 之前聊过 xx 吗 / 上次说的 xx 是啥"这类问题时，
        先调本工具——这是 agent 主动回溯历史对话的入口。

        与 search_memory 的区别：search_memory 查的是你之前主动归档的"事实笔记"，
        本工具直查 agent_message 表的对话流水。要找"我们当时聊了啥"用本工具；
        要找"我记下过哪条事实"用 search_memory。

        每条命中返回 sessionKey / sessionTitle / messageId / role / excerpt（关键词上下文）
        / createTime。回复用户时报具体时间和会话标题，必要时再用 get_session_messages 拉详情。
        """
        keyword = (query or "").strip()
        if not keyword:
            return _err("query 不能为空")

        top_k = max(1, min(int(top_k or 10), 30))

        # 1) 圈定本人未删会话
        session_qs = AgentSession.filter(user_id=user_id, is_deleted=0)
        if not include_current_session and current_session_key:
            session_qs = session_qs.exclude(session_key=current_session_key)
        sessions = await session_qs.all()
        if not sessions:
            return _ok([])
        sid_to_session = {s.id: s for s in sessions}

        # 2) 在这些会话的消息表里 LIKE 命中（只看 user / assistant；batch 是聚合行没意义）
        msg_qs = AgentMessage.filter(
            session_id__in=list(sid_to_session.keys()),
            role__in=["user", "assistant"],
            content__icontains=keyword,
        )
        if days and days > 0:
            since = datetime.now() - timedelta(days=int(days))
            msg_qs = msg_qs.filter(create_time__gte=since)
        # 多取一些再裁剪，确保 top_k 是真正最新的命中
        msgs = await msg_qs.order_by("-id").limit(top_k * 2)

        items: list[dict] = []
        for m in msgs:
            sess = sid_to_session.get(m.session_id)
            if sess is None:
                continue
            items.append({
                "sessionKey": sess.session_key,
                "sessionTitle": sess.title,
                "messageId": m.id,
                "role": m.role,
                "excerpt": _excerpt(m.content or "", keyword),
                "createTime": m.create_time.isoformat() if m.create_time else None,
            })
            if len(items) >= top_k:
                break
        return _ok(items)

    @tool
    async def list_recent_sessions(
        limit: Annotated[int, "返回会话数，默认 20，最大 50"] = 20,
        days: Annotated[
            Optional[int],
            "时间窗口（天）。仅列最近 N 天有更新的会话；不传则按 update_time 倒序取最新。",
        ] = None,
        title_contains: Annotated[
            Optional[str],
            "可选：标题包含的关键词（LIKE 模糊匹配）。比如想找『关于报表的那个对话』就传『报表』。",
        ] = None,
        include_current_session: Annotated[
            bool,
            "是否包含当前会话，默认 False",
        ] = False,
    ) -> str:
        """
        列出当前用户最近的会话（仅本人未删，按 update_time 倒序）。

        用户问"我昨天 / 上周聊了啥""有没有标题里带 xx 的对话"时调它。
        每条返回：sessionKey / title / messageCount / createTime / updateTime。
        若需要看会话内容，再用 get_session_messages(session_key) 取细节。
        """
        limit = max(1, min(int(limit or 20), 50))
        qs = AgentSession.filter(user_id=user_id, is_deleted=0)
        if not include_current_session and current_session_key:
            qs = qs.exclude(session_key=current_session_key)
        kw = (title_contains or "").strip()
        if kw:
            qs = qs.filter(title__icontains=kw)
        if days and days > 0:
            since = datetime.now() - timedelta(days=int(days))
            qs = qs.filter(update_time__gte=since)
        rows = await qs.order_by("-update_time").limit(limit)
        items = [
            {
                "sessionKey": s.session_key,
                "title": s.title,
                "messageCount": s.message_count,
                "createTime": s.create_time.isoformat() if s.create_time else None,
                "updateTime": s.update_time.isoformat() if s.update_time else None,
            }
            for s in rows
        ]
        return _ok(items)

    @tool
    async def get_session_messages(
        session_key: Annotated[
            str,
            "目标会话 key（从 search_chat_history / list_recent_sessions 的返回里拿）",
        ],
        limit: Annotated[int, "最多返回多少条消息，默认 50，最大 200"] = 50,
        roles: Annotated[
            Optional[list[str]],
            "限制角色：user / assistant / batch；不传则全部",
        ] = None,
    ) -> str:
        """
        读取指定会话的消息时间线（按 id 升序）。仅当前用户名下会话可读。

        典型场景：先用 search_chat_history 命中某个会话后，需要"重述当时的结论"或"接着
        上次往下聊"——调本工具把那段上下文捞回来再回答用户。
        """
        s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
        if s is None:
            return _err("会话不存在或已删除")
        if s.user_id is not None and s.user_id != user_id:
            return _err("无权访问：会话不属于当前用户")
        limit = max(1, min(int(limit or 50), 200))
        qs = AgentMessage.filter(session_id=s.id)
        if roles:
            allowed = [r for r in roles if r in ("user", "assistant", "batch")]
            if allowed:
                qs = qs.filter(role__in=allowed)
        rows = await qs.order_by("id").limit(limit)
        items = [
            {
                "messageId": m.id,
                "role": m.role,
                # 单条消息 content 可能很长，截断防 token 爆炸；要看全文用 get_message_detail(message_id)
                "content": (m.content or "")[:2000],
                "createTime": m.create_time.isoformat() if m.create_time else None,
                "status": m.status,
            }
            for m in rows
        ]
        return _ok({
            "sessionKey": s.session_key,
            "title": s.title,
            "totalCount": s.message_count,
            "returned": len(items),
            "messages": items,
        })

    @tool
    async def get_message_detail(
        message_id: Annotated[
            int,
            "消息 ID（从 search_chat_history / get_session_messages 返回的 messageId 里拿）",
        ],
    ) -> str:
        """
        读取单条历史消息的完整原文（不截断）。仅当前用户名下会话的消息可读。

        get_session_messages 为控 token 会把每条截断到 2000 字符；被截断的消息
        （结尾带 "…"）需要看全文、或用户要求"把当时那条完整发给我"时，调本工具。
        返回：messageId / sessionKey / sessionTitle / role / content（全文）/ createTime / status。
        """
        m = await AgentMessage.get_or_none(id=message_id)
        if m is None:
            return _err("消息不存在")
        s = await AgentSession.get_or_none(id=m.session_id, is_deleted=0)
        if s is None or (s.user_id is not None and s.user_id != user_id):
            return _err("无权访问：消息不属于当前用户")
        return _ok({
            "messageId": m.id,
            "sessionKey": s.session_key,
            "sessionTitle": s.title,
            "role": m.role,
            "content": m.content or "",
            "createTime": m.create_time.isoformat() if m.create_time else None,
            "status": m.status,
        })

    # with_lenient_args：兼容模型把 roles 等数组参数传成 JSON 字符串（如 "user,assistant"）
    from app.langchain.tools._tool_args import with_lenient_args

    return [with_lenient_args(t) for t in [search_chat_history, list_recent_sessions, get_session_messages, get_message_detail]]


__all__ = ["make_history_tools"]
