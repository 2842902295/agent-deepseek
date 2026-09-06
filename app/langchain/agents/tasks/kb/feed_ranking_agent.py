"""
知识库 · 夜间 feed 策展 agent

每天凌晨 03:17 由 APScheduler 触发；也可由 API /feed/rerun 手动触发。

跟早期"全量塞 prompt"版本的差别：
  - agent 自己判断今天的主题（先看紧迫信号 + 最近 QA + 昨日序）
  - 再用 search_my_kb / get_due_todos / get_recent_qa_topics / ... 等工具按需取证据
  - 最后输出 FeedRankingResult（结构化）

agent 关注的是"策展"——挑出值得用户明天看到的东西并配上理由——
而不是机械排序。让 LLM 真正使用工具去探索 KB，而不是被动接收一堆 entries。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta
from typing import Annotated, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool

from app.api.v1.ai.kb import _read_tags
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.llm_providers import get_llm
from app.models.standard.agent import AgentMessage, AgentSession
from app.models.standard.nian import NianDailyFeed, NianFeedRunLog
from app.services.seekdb import kb_get, kb_hybrid_search, kb_list, kb_upsert

# ── 输出 schema ──────────────────────────────────────────────────────────────


class _RankItem(BaseModel):
    entry_id: str = Field(..., description="KBEntry id")
    rank: int = Field(..., description="序号，0 = 第一位")
    reason: str = Field(..., description="徽标理由，中文，≤14 字")
    confidence: float = Field(0.7, description="0-1，对该位次的把握")


class FeedRankingResult(BaseModel):
    feed_date: str = Field(..., description="ISO 日期，YYYY-MM-DD")
    items: list[_RankItem] = Field(default_factory=list, description="按序排好的条目")
    brief: str = Field("", description="1~2 句中文晨间简报")


# ── 常量 ─────────────────────────────────────────────────────────────────────


_MAX_FEED_ITEMS = 50
_MAX_AGENT_STEPS = 24  # 给工具调用留足空间
_TITLE_LEN = 80
_SUMMARY_LEN = 160


# ── 公共辅助 ─────────────────────────────────────────────────────────────────


def _truncate(text: Optional[str], n: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _entry_brief(entry_id: str, meta: dict) -> dict:
    """喂给 LLM 的精简条目结构，剔除噪声字段。"""
    tags = _read_tags(meta)
    try:
        extra = json.loads(meta.get("meta_json") or "{}")
        if not isinstance(extra, dict):
            extra = {}
    except Exception:
        extra = {}
    interactions = extra.get("interactions") or {}
    opened = (interactions.get("opened_at") or []) if isinstance(interactions, dict) else []
    double_tap = (interactions.get("double_tap_at") or []) if isinstance(interactions, dict) else []
    return {
        "id": entry_id,
        "type": meta.get("entry_type") or "knowledge",
        "title": _truncate(meta.get("title"), _TITLE_LEN),
        "summary": _truncate(meta.get("summary"), _SUMMARY_LEN),
        "tags": tags,
        "updated_at": meta.get("updated_at") or 0,
        "due_at": meta.get("due_at") or 0,
        "todo_status": meta.get("todo_status") or "",
        "last_feed_rank": (
            meta.get("last_feed_rank") if meta.get("last_feed_rank", -1) != -1 else None
        ),
        "last_feed_reason": meta.get("last_feed_reason") or "",
        "open_count": len(opened) if isinstance(opened, list) else 0,
        "double_tap_count": len(double_tap) if isinstance(double_tap, list) else 0,
    }


def _list_user_entries_raw(user_id: int) -> list[dict]:
    """全量未归档的该用户 entry 原始字典 [{id, metadata}]。"""
    rows = kb_list(limit=10_000, offset=0)
    out: list[dict] = []
    for r in rows:
        meta = r.get("metadata") or {}
        if meta.get("user_id") != user_id:
            continue
        if meta.get("is_archived"):
            continue
        out.append({"id": r["id"], "metadata": meta})
    return out


# ── 工具集（闭包绑定 user_id / today） ───────────────────────────────────────


def _build_tools(user_id: int, today: date):
    """
    给 ranking agent 用的只读工具集。所有工具：
      - 闭包绑定 user_id，agent 不可见、也无法越权
      - seekdb / Tortoise 调用一律 to_thread 包裹（同步阻塞 RPC）
      - 输出统一 JSON 字符串，便于 LLM 引用
    """

    @tool
    async def list_kb_overview(
        limit: Annotated[int, "最多返回多少条，默认 80"] = 80,
        sort_by: Annotated[str, "排序方式：updated（最近更新）/ due（截止时间）"] = "updated",
        entry_type: Annotated[
            Optional[str], "可选过滤：knowledge / idea / todo"
        ] = None,
    ) -> str:
        """
        浏览当前用户的笔记/灵感/待办全貌。返回精简 brief（id + title + type + due_at + last_feed_*）。
        策展前 first call —— 先用这个看清候选池有什么，再决定用 search 还是按主题深挖。
        """
        rows = await asyncio.to_thread(_list_user_entries_raw, user_id)
        items = [_entry_brief(r["id"], r["metadata"]) for r in rows]
        if entry_type:
            items = [x for x in items if x["type"] == entry_type]
        if sort_by == "due":
            items.sort(key=lambda x: x["due_at"] or 10**15)
        else:
            items.sort(key=lambda x: x["updated_at"] or 0, reverse=True)
        return json.dumps(
            {"total": len(items), "items": items[:limit]}, ensure_ascii=False
        )

    @tool
    async def search_my_kb(
        query: Annotated[str, "自然语言查询，会走 fulltext + 语义 hybrid 检索"],
        top_k: Annotated[int, "返回前几条，默认 8"] = 8,
    ) -> str:
        """
        语义+关键字 hybrid search 用户自己的 KB。用于"用户最近在想 X，KB 里有没有相关的 entry"。
        """

        def _search() -> list[dict]:
            hits = kb_hybrid_search(
                query_text=query,
                n_results=max(top_k * 3, top_k),  # 多召一些再过滤
                where={"user_id": user_id},
            )
            out: list[dict] = []
            for h in hits:
                meta = h.get("metadata") or {}
                if meta.get("is_archived"):
                    continue
                if meta.get("user_id") != user_id:
                    continue
                brief = _entry_brief(h["id"], meta)
                brief["distance"] = h.get("distance")
                out.append(brief)
            return out[:top_k]

        items = await asyncio.to_thread(_search)
        return json.dumps({"query": query, "items": items}, ensure_ascii=False)

    @tool
    async def get_due_todos(
        within_days: Annotated[int, "未来多少天内到期算紧迫，默认 7"] = 7,
        include_overdue: Annotated[bool, "是否包含已逾期未完成的"] = True,
    ) -> str:
        """
        紧迫待办（含已逾期）。返回 due_at 升序的 todo brief 列表。
        策展时优先把这些放最前。
        """
        now_ms = int(time.time() * 1000)
        cutoff = now_ms + within_days * 86400 * 1000

        def _fetch() -> list[dict]:
            rows = _list_user_entries_raw(user_id)
            out: list[dict] = []
            for r in rows:
                meta = r["metadata"]
                if (meta.get("entry_type") or "") != "todo":
                    continue
                if (meta.get("todo_status") or "") == "done":
                    continue
                due = meta.get("due_at") or 0
                if not due:
                    continue
                is_overdue = due < now_ms
                if not is_overdue and due > cutoff:
                    continue
                if is_overdue and not include_overdue:
                    continue
                brief = _entry_brief(r["id"], meta)
                brief["is_overdue"] = is_overdue
                brief["days_to_due"] = round((due - now_ms) / 86400 / 1000, 1)
                out.append(brief)
            out.sort(key=lambda x: x["due_at"])
            return out

        items = await asyncio.to_thread(_fetch)
        return json.dumps({"items": items}, ensure_ascii=False)

    @tool
    async def get_recent_qa_topics(
        days: Annotated[int, "最近多少天，默认 7"] = 7,
        limit: Annotated[int, "最多返回多少条 message，默认 30"] = 30,
    ) -> str:
        """
        用户最近 N 天的 QA 对话简摘（user/assistant 轮次，截 200 字）。
        agent 据此推测"用户最近在想什么主题"，再用 search_my_kb 找相关条目。
        """
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        sessions = await AgentSession.filter(user_id=user_id, is_deleted=0).all()
        if not sessions:
            return json.dumps({"items": []}, ensure_ascii=False)
        sids = [s.id for s in sessions]
        msgs = (
            await AgentMessage.filter(session_id__in=sids)
            .order_by("-id")
            .limit(max(limit * 2, limit))
            .all()
        )
        out: list[dict] = []
        for m in msgs:
            ts = int(m.create_time.timestamp() * 1000) if m.create_time else 0
            if ts < cutoff_ms:
                continue
            out.append({
                "role": m.role,
                "content": _truncate(m.content, 200),
                "ts": ts,
            })
            if len(out) >= limit:
                break
        out.reverse()
        return json.dumps({"items": out}, ensure_ascii=False)

    @tool
    async def get_yesterday_feed() -> str:
        """
        昨日已经定的 feed 序（entry_id + rank + reason）。
        策展时参考它保持稳定性——除非有强信号否则别大洗牌。
        """
        yesterday = today - timedelta(days=1)
        rows = (
            await NianDailyFeed.filter(user_id=user_id, feed_date=yesterday)
            .order_by("rank")
            .all()
        )
        items = [
            {"entry_id": r.entry_id, "rank": r.rank, "reason": r.reason}
            for r in rows
        ]
        return json.dumps({"items": items}, ensure_ascii=False)

    @tool
    async def get_low_engagement_entries(
        idle_days: Annotated[int, "updated_at 距今多少天算沉底，默认 30"] = 30,
        feed_lookback_days: Annotated[int, "向前多少天的 feed 视为近期，默认 14"] = 14,
        max_open_count: Annotated[int, "open_count ≤ 这个值才算低参与，默认 1"] = 1,
        limit: Annotated[int, "最多返回多少条，默认 12"] = 12,
        entry_type: Annotated[
            Optional[str], "可选过滤：knowledge / idea / todo"
        ] = None,
    ) -> str:
        """
        「沉底好东西」候选池：updated_at 老 + 用户基本没打开 + 最近的 feed 没收录。
        用于「沉底捞起」原则——从这批里挑 1~3 条放进 feed，搭配 `search_my_kb`
        判断和最近主题是否相关，避免捞出无关老古董。

        返回每条都附 idle_days / open_count，便于你给徽标写理由。
        """
        now_ms = int(time.time() * 1000)
        idle_cutoff_ms = now_ms - idle_days * 86400 * 1000

        # 取近期 feed 涉及到的 entry_id 集合
        feed_since = today - timedelta(days=feed_lookback_days)
        recent_feed_rows = (
            await NianDailyFeed.filter(
                user_id=user_id, feed_date__gte=feed_since
            )
            .values_list("entry_id", flat=True)
        )
        recent_feed_ids = {eid for eid in recent_feed_rows}

        def _fetch() -> list[dict]:
            rows = _list_user_entries_raw(user_id)
            out: list[dict] = []
            for r in rows:
                meta = r["metadata"]
                eid = r["id"]
                if eid in recent_feed_ids:
                    continue
                updated_at = meta.get("updated_at") or 0
                if updated_at and updated_at > idle_cutoff_ms:
                    continue
                if entry_type and (meta.get("entry_type") or "") != entry_type:
                    continue
                brief = _entry_brief(eid, meta)
                if brief["open_count"] > max_open_count:
                    continue
                # 已完成的 todo 不在沉底捞起范围
                if brief["type"] == "todo" and brief.get("todo_status") == "done":
                    continue
                idle_ms = now_ms - (updated_at or now_ms)
                brief["idle_days"] = round(idle_ms / 86400 / 1000, 1)
                out.append(brief)
            # idle_days 大 + open_count 小 优先
            out.sort(
                key=lambda x: (-(x.get("idle_days") or 0), x.get("open_count", 0))
            )
            return out[:limit]

        items = await asyncio.to_thread(_fetch)
        return json.dumps({"items": items}, ensure_ascii=False)

    @tool
    async def get_entry_detail(
        entry_id: Annotated[str, "条目 id"],
    ) -> str:
        """
        取一条 entry 的完整字段（title/summary/content/tags/meta/interactions）。
        前面工具只给了 summary，需要更详细信息时再用这个。
        """

        def _get() -> dict:
            raw = kb_get(entry_id)
            if not raw:
                return {"ok": False, "error": "not found"}
            meta = raw.get("metadata") or {}
            if meta.get("user_id") != user_id:
                return {"ok": False, "error": "not yours"}
            tags = _read_tags(meta)
            try:
                extra = json.loads(meta.get("meta_json") or "{}")
                if not isinstance(extra, dict):
                    extra = {}
            except Exception:
                extra = {}
            interactions = extra.get("interactions") or {}
            return {
                "ok": True,
                "id": entry_id,
                "type": meta.get("entry_type") or "knowledge",
                "title": meta.get("title") or "",
                "summary": meta.get("summary") or "",
                "content": _truncate(meta.get("content"), 1200),
                "tags": tags,
                "updated_at": meta.get("updated_at") or 0,
                "due_at": meta.get("due_at") or 0,
                "todo_status": meta.get("todo_status") or "",
                "last_feed_rank": (
                    meta.get("last_feed_rank") if meta.get("last_feed_rank", -1) != -1 else None
                ),
                "last_feed_reason": meta.get("last_feed_reason") or "",
                "interactions": interactions,
            }

        return json.dumps(await asyncio.to_thread(_get), ensure_ascii=False)

    return [
        list_kb_overview,
        search_my_kb,
        get_due_todos,
        get_recent_qa_topics,
        get_yesterday_feed,
        get_low_engagement_entries,
        get_entry_detail,
    ]


# ── system prompt ────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
你是用户私人知识库「知识库」的内容编辑。每天，你都要替这位用户重新策展一份明日的 feed，
让他打开 app 时，最该看的内容浮在最前面——尤其是那些"好东西，但容易沉底"的条目。

## 你不是排序器，你是编辑

你的工作不是机械地给一堆 entries 排号。我已经给了你一组工具去**主动探索**这位用户的知识库：

- `get_recent_qa_topics`：先看用户最近 7 天和 AI 聊了什么——这是他当下在想的事
- `get_due_todos`：哪些待办今天/这周到期、哪些已逾期
- `get_yesterday_feed`：昨日的序，作为稳定性参考
- `list_kb_overview`：全量浏览候选池（精简版，只看 title+meta）
- `search_my_kb(query)`：发现用户最近在想 X，就用这个去 KB 里找和 X 相关的条目
- `get_low_engagement_entries`：「沉底好东西」候选池——updated 老 + open 少 + 最近没进 feed
- `get_entry_detail(id)`：需要细节再单独取

## 推荐流程

1. **先了解今日主题**：调 `get_recent_qa_topics` + `get_due_todos`，再 `get_yesterday_feed`
2. **决定主线**：今天的 feed 是「围绕用户最近问的 X」、还是「紧迫待办收尾」？通常二者并存
3. **拉证据**：根据主题用 `search_my_kb` 拉相关 entries；同时 `list_kb_overview` 看看有没有
   "好东西但很久没动"的（updated_at 旧 + open_count 低 + 不在昨日 feed）——这些应该被捞起来
4. **组装 feed**：≤50 条，每条配一句 ≤14 字理由徽标

## 必须遵守的硬约束

- **不要把 dismissed 的条目放进 feed**（工具不会返回它们，除非你主动 `get_entry_detail` 去找）
- **限额 50 条**：超出的舍弃
- **稳定优先**：昨日 feed 的相对位置尽量保留，除非有新证据要求重排
- **每条都要有可核验的 reason**：理由必须能从你工具调用得到的事实推出，不要瞎编
  - 紧迫："今天到期" / "已逾期 N 天" / "明天到期"
  - 关联："和最近对话相关" / "你昨天问过 X" / "刚才聊到"
  - 沉底捞起："好久没看" / "可能想起这个" / "之前 pin 过没看"
  - 高频："常被点开" / "你反复看的"

## 关于「沉底捞起」（这是用户最看重的能力）

用户笔记会越积越多。一些好东西会被新内容压下去——
- 长时间没 updated 但 open_count 低
- 不在最近 feed 里
- 但语义和最近 QA 主题相关 / 类型是 knowledge 或 idea（不是 todo）

直接调 `get_low_engagement_entries` 拿候选池，从中挑 1~3 条。
**判断相关性时**：拿到候选 id 列表后，用 `search_my_kb` 拿最近 QA 主题去搜，
两者交集的就是"沉底但和当下相关"的，优先这些；
如果交集为空，就单纯按 idle_days 大、open_count 小的挑，理由用「好久没看」。
不要塞太多——优先级是：紧迫 todo > 和最近聊天相关 > 沉底捞起 > 其他。

## brief

用 1~2 句中文写晨间简报，描述这次 feed 的主题：
> 今天有 2 条标准比对的待办即将到期，一条灵感和你最近在想的 LLM 评估相关。
不要客套，直接讲事实。

## 输出

调够工具后，输出 FeedRankingResult JSON：
- feed_date: 今天的 ISO 日期
- items: [{entry_id, rank(从 0 开始连续), reason, confidence}]
- brief: 简报

只能引用工具调用得到的真实 entry_id，不能编。
"""


# ── 主入口 ───────────────────────────────────────────────────────────────────


def _writeback_meta(entry_id: str, rank: int, reason: str) -> None:
    """把 last_feed_rank / last_feed_reason 回写到 KBEntry metadata。"""
    raw = kb_get(entry_id)
    if not raw:
        return
    meta = dict(raw.get("metadata") or {})
    meta["last_feed_rank"] = int(rank)
    meta["last_feed_reason"] = reason or ""
    document = (
        (meta.get("title") or "")
        + ("\n\n" + meta["summary"] if meta.get("summary") else "")
        + ("\n\n" + meta["content"] if meta.get("content") else "")
    )
    kb_upsert(entry_id, document, meta)


async def rank_feed_for_user(*, user_id: int, feed_date: date) -> int:
    """
    给一个用户跑一次策展，返回写入条数。

    流程：
      1. 给 agent 配上工具，让它自己探索、决定 feed
      2. 拿 FeedRankingResult，做最小清洗（id 合法 / 去重 / rank 重编 / 限额）
      3. 重置当日 feed 表 + 回写 KBEntry 的 last_feed_*
      4. 写一行 NianFeedRunLog
    """
    started = time.time()

    # 跑前快速看看用户有没有内容；空账户直接跳过 agent 调用，省 LLM
    raw_entries = await asyncio.to_thread(_list_user_entries_raw, user_id)
    if not raw_entries:
        await NianFeedRunLog.create(
            user_id=user_id,
            feed_date=feed_date,
            status="skipped",
            items_written=0,
            brief="",
            error="no entries",
            duration_ms=int((time.time() - started) * 1000),
        )
        return 0

    valid_ids = {r["id"] for r in raw_entries}

    weekdays_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    user_message = (
        f"今天是 {feed_date.isoformat()}（{weekdays_cn[feed_date.weekday()]}）。\n"
        f"用户当前知识库共 {len(raw_entries)} 条未归档 entry。\n"
        "按 system 中的流程探索并策展明日 feed。"
    )

    tools = _build_tools(user_id=user_id, today=feed_date)
    agent = create_agent(
        model=get_llm(),
        tools=tools,
        system_prompt=_SYSTEM_PROMPT,
        response_format=ToolStrategy(schema=FeedRankingResult, handle_errors=True),
    )

    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config={"recursion_limit": _MAX_AGENT_STEPS * 2},
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[NianFeedRanking] user={user_id} agent ainvoke 失败: {e}")
        await NianFeedRunLog.create(
            user_id=user_id,
            feed_date=feed_date,
            status="failed",
            items_written=0,
            brief="",
            error=str(e)[:2000],
            duration_ms=int((time.time() - started) * 1000),
        )
        return 0

    report: Optional[FeedRankingResult] = result.get("structured_response")
    if report is None:
        await NianFeedRunLog.create(
            user_id=user_id,
            feed_date=feed_date,
            status="failed",
            items_written=0,
            brief="",
            error="no structured_response",
            duration_ms=int((time.time() - started) * 1000),
        )
        return 0

    # 清洗：只保留属于该用户的 id，去重 + 限额 + rank 重编
    items_clean: list[_RankItem] = []
    seen: set[str] = set()
    for it in report.items:
        if it.entry_id not in valid_ids or it.entry_id in seen:
            continue
        seen.add(it.entry_id)
        items_clean.append(it)
        if len(items_clean) >= _MAX_FEED_ITEMS:
            break
    items_clean = [
        _RankItem(
            entry_id=it.entry_id,
            rank=i,
            reason=(it.reason or "")[:80],
            confidence=max(0.0, min(1.0, float(it.confidence or 0))),
        )
        for i, it in enumerate(items_clean)
    ]

    # 写库
    await NianDailyFeed.filter(user_id=user_id, feed_date=feed_date).delete()
    if items_clean:
        await NianDailyFeed.bulk_create([
            NianDailyFeed(
                user_id=user_id,
                feed_date=feed_date,
                entry_id=it.entry_id,
                rank=it.rank,
                reason=it.reason,
                confidence=it.confidence,
            )
            for it in items_clean
        ])
        for it in items_clean:
            try:
                await asyncio.to_thread(_writeback_meta, it.entry_id, it.rank, it.reason)
            except Exception as _e:  # noqa: BLE001
                logger.warning(f"[NianFeedRanking] 回写 entry meta 失败 {it.entry_id}: {_e}")

    await NianFeedRunLog.create(
        user_id=user_id,
        feed_date=feed_date,
        status="ok",
        items_written=len(items_clean),
        brief=(report.brief or "")[:2000],
        error=None,
        duration_ms=int((time.time() - started) * 1000),
    )
    return len(items_clean)


__all__ = ["rank_feed_for_user", "FeedRankingResult"]
