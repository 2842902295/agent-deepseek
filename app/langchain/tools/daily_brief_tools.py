"""
每日简报专用工具

只包含两个工具：
- get_recent_sessions：查询用户近期会话和消息摘要
- get_prev_daily_brief：获取上一次简报内容
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Annotated, Optional

from langchain.tools import tool
from loguru import logger

import aiomysql
from app.services.mysql_pool import standard_pool


async def make_daily_brief_tools(user_id: int) -> list:
    """创建每日简报所需工具（绑定 user_id）"""

    @tool
    async def get_recent_sessions(
        days: Annotated[int, "查询最近多少天的会话，默认 14 天"] = 14,
    ) -> str:
        """查询当前用户最近 N 天的会话列表，包含每个会话的标题、时间和消息摘要（仅取前后几条消息用于话题识别）。"""
        since = date.today() - timedelta(days=days)
        try:
            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    # 查会话列表
                    await cur.execute(
                        """
                        SELECT id, session_key, title, message_count, create_time, update_time
                        FROM agent_session
                        WHERE user_id = %s
                          AND is_deleted = 0
                          AND create_time >= %s
                        ORDER BY create_time DESC
                        LIMIT 30
                        """,
                        (user_id, since),
                    )
                    sessions = await cur.fetchall()

                    if not sessions:
                        return "最近没有会话记录。"

                    results = []
                    for s in sessions:
                        sid = s["id"]
                        # 每个会话取首尾各 2 条消息做话题摘要
                        await cur.execute(
                            """
                            (SELECT role, LEFT(content, 300) AS snippet, create_time
                             FROM agent_message
                             WHERE session_id = %s AND status = 'done' AND role IN ('user', 'assistant')
                             ORDER BY id ASC LIMIT 2)
                            UNION ALL
                            (SELECT role, LEFT(content, 300) AS snippet, create_time
                             FROM agent_message
                             WHERE session_id = %s AND status = 'done' AND role IN ('user', 'assistant')
                             ORDER BY id DESC LIMIT 2)
                            ORDER BY create_time ASC
                            """,
                            (sid, sid),
                        )
                        msgs = await cur.fetchall()
                        snippets = [
                            f"[{m['role']}] {(m['snippet'] or '').strip()}"
                            for m in msgs
                            if m["snippet"]
                        ]
                        results.append(
                            {
                                "session_key": s["session_key"],
                                "title": s["title"],
                                "message_count": s["message_count"],
                                "created_at": str(s["create_time"])[:16],
                                "updated_at": str(s["update_time"])[:16],
                                "snippets": snippets,
                            }
                        )

            return json.dumps(results, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("get_recent_sessions 失败")
            return f"查询失败：{e}"

    @tool
    async def get_prev_daily_brief() -> str:
        """获取当前用户上一次（最近一条）每日简报的日期和完整内容，用于识别持续话题并形成连贯叙述。"""
        try:
            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        """
                        SELECT id, brief_date, content_html, topics_json, ref_session_keys
                        FROM agent_daily_brief
                        WHERE user_id = %s AND generation_status = 'done'
                        ORDER BY brief_date DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    row = await cur.fetchone()

            if not row:
                return "用户没有历史简报，这是第一次生成。"

            topics = row["topics_json"] or []
            if isinstance(topics, str):
                topics = json.loads(topics)

            return json.dumps(
                {
                    "brief_date": str(row["brief_date"]),
                    "topics": topics,
                    "content_html": (row["content_html"] or "")[:3000],
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            logger.exception("get_prev_daily_brief 失败")
            return f"查询失败：{e}"

    return [get_recent_sessions, get_prev_daily_brief]
