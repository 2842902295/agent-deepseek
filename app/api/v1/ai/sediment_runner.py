"""
沉淀运行时：所有沉淀类端点共用的"调 qa_agent + 跑 sediment skill"逻辑。

设计：
- 复用 qa_agent 实例（带 SQLite checkpointer），thread_id 与原会话一致——agent 自带对话记忆，无需预拼 transcript
- 触发 user message 直接进 langgraph checkpoint（不写 AgentMessage 表，前端不渲染）
- agent 通过 SkillsMiddleware 自己识别 sediment skill 的 description、按需读 SKILL.md
- agent 输出末尾必须有 <sediment-report>{...}</sediment-report> marker；从 marker 抠 JSON 反给前端
- marker 抓不到时，从工具调用历史里反查（skill_save 的返回文本）做兜底
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from typing import Any, Optional

from loguru import logger

from app.api.v1.ai.qa import _get_agent_for_session

# 为不需要绑定具体会话的入口（万用收件箱）使用的"匿名沉淀"工作区前缀
_ANON_SESSION_PREFIX = "sed_anon_"
_REPORT_MARKER = re.compile(r"<sediment-report>\s*(\{.*?\})\s*</sediment-report>", re.DOTALL)


def _extract_report(text: str) -> Optional[dict[str, Any]]:
    """从 agent 最终回复里抠 <sediment-report>{...}</sediment-report> JSON。"""
    if not text:
        return None
    m = _REPORT_MARKER.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        logger.warning(f"[sediment] marker JSON 解析失败：{m.group(1)[:200]}")
        return None


def _final_ai_text(result: Any) -> str:
    """从 agent.ainvoke 返回里取最后一条 AI 消息的纯文本。"""
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(msgs or []):
        c = getattr(msg, "content", "")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        if c and getattr(msg, "type", "") == "ai":
            return str(c).strip()
    return ""


# skill_save 返回形如「技能 @<key> 已创建（id=..）」/「技能 @<key> 已更新字段：..」
_SKILL_KEY_FROM_TOOL = re.compile(r"技能\s*@([^\s（(]+)\s+已(?:创建|更新)")


def _recover_report_from_tools(result: Any) -> Optional[dict[str, Any]]:
    """
    marker 抠不到时的工具调用兜底：扫所有 ToolMessage，看 skill_save
    是否真的产出了技能记录；产出了就重建一份 report。
    """
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    skill_key: Optional[str] = None
    for msg in msgs or []:
        msg_type = getattr(msg, "type", "")
        if msg_type != "tool":
            continue
        name = getattr(msg, "name", "") or ""
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
        content = str(content or "")
        if name == "skill_save":
            m = _SKILL_KEY_FROM_TOOL.search(content)
            if m:
                skill_key = m.group(1)

    if not skill_key:
        return None

    return {
        "type": "skill",
        "skill_key": skill_key,
        "has_files": False,
        "summary": f"已凝练为技能「{skill_key}」（自动恢复，AI 未输出标准报告）",
        "_recovered": True,
    }


async def run_sediment(
    *,
    trigger_text: str,
    session_key: Optional[str],
    user_id: Optional[int],
) -> dict[str, Any]:
    """
    跑一次沉淀。返回 {ok, report, raw} ——
      - ok=True：report 是 marker 抠出来的 JSON
      - ok=False：report 给个 fallback summary，raw 是 agent 原文便于排查

    隔离策略（避免每次沉淀污染主对话历史）：
      1. 拿主 thread 的全部 messages 作为 ainvoke 的初始输入
      2. 沉淀跑在一个**全新的临时 thread_id** 上，它的 checkpoint 与主 thread 不相干
      3. 跑完顺手清理临时 thread 的 checkpoint，释放 SQLite 空间
    """
    if session_key:
        sk = session_key
        main_thread_id = f"qa-{sk}"
    else:
        sk = f"{_ANON_SESSION_PREFIX}{secrets.token_hex(4)}"
        main_thread_id = None

    agent = await _get_agent_for_session(sk, user_id)

    # 准备初始消息：主 thread 历史（如果有）+ 本次 trigger
    initial_messages: list = []
    if main_thread_id:
        try:
            main_state = await agent.aget_state(
                {"configurable": {"thread_id": main_thread_id}}
            )
            initial_messages = list(main_state.values.get("messages") or [])
        except Exception:
            logger.exception("[sediment] 读取主对话 state 失败，沉淀将不带历史继续")

    initial_messages.append({"role": "user", "content": trigger_text})

    tmp_thread_id = f"sediment-tmp-{secrets.token_hex(6)}"
    config = {
        "configurable": {
            "thread_id": tmp_thread_id,
            "user_id": str(user_id) if user_id else "0",
        }
    }

    # 设置 CTX_USER_ID，确保 agent 工具（如 skill_save）能拿到正确的 user_id
    from app.core.ctx import CTX_USER_ID
    original_user_id = CTX_USER_ID.get()
    if user_id is not None:
        CTX_USER_ID.set(user_id)

    try:
        try:
            result = await agent.ainvoke({"messages": initial_messages}, config=config)
        except Exception as e:  # noqa: BLE001
            logger.exception("[sediment] agent.ainvoke 失败")
            return {
                "ok": False,
                "report": {"candidates": 0, "summary": f"沉淀失败：{e}", "results": []},
                "raw": "",
            }

        raw = _final_ai_text(result)
        report = _extract_report(raw)
        if report is None:
            # 兜底：从工具调用历史反查产物（agent 实际可能创建成功了，只是没吐合规 marker）
            recovered = _recover_report_from_tools(result)
            if recovered is not None:
                logger.info(
                    f"[sediment] 未抓到 marker，但工具调用反查到 skill_key={recovered.get('skill_key')}，自动恢复"
                )
                return {"ok": True, "report": recovered, "raw": raw}
            logger.warning(f"[sediment] 未在 AI 回复里找到 marker 也未反查到产物：{raw[:300]}")
            return {
                "ok": False,
                "report": {"candidates": 0, "summary": "沉淀完成但未拿到结构化报告", "results": []},
                "raw": raw,
            }

        return {"ok": True, "report": report, "raw": raw}

    finally:
        # 恢复原 CTX_USER_ID
        CTX_USER_ID.set(original_user_id)
        await _cleanup_tmp_thread(agent, tmp_thread_id)


async def _cleanup_tmp_thread(agent: Any, thread_id: str) -> None:
    """删除临时 thread 在 SQLite checkpointer 里的所有 checkpoint。

    优先用 langgraph 内建 adelete_thread；fallback 直接走 SQL DELETE（langgraph
    schema 的 thread_id 字段是公开约定）。失败不抛——清理是 best effort。
    """
    inner = getattr(agent, "_inner", agent)
    checkpointer = getattr(inner, "checkpointer", None)
    if checkpointer is None:
        return
    try:
        adelete = getattr(checkpointer, "adelete_thread", None)
        if adelete is not None:
            await adelete(thread_id)
            return
    except Exception:
        logger.warning(f"[sediment] adelete_thread({thread_id}) 失败，回退 SQL", exc_info=True)

    try:
        conn = getattr(checkpointer, "conn", None)
        if conn is None:
            return
        for table in ("checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs"):
            try:
                await conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
            except Exception:
                pass
        await conn.commit()
    except Exception:
        logger.warning(f"[sediment] 清理 tmp thread {thread_id} 的 SQL 删除失败", exc_info=True)


def normalize_kb_payload(report: dict[str, Any]) -> dict[str, Any]:
    """模式 A 的 report 标准化成 KBSedimentResult 形态：{candidates, summary, results}。"""
    return {
        "candidates": int(report.get("candidates") or 0),
        "summary": report.get("summary") or "",
        "results": list(report.get("results") or []),
    }


# ── SSE helpers ──────────────────────────────────────────────────────────────


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_sediment(
    *,
    trigger_text: str,
    session_key: Optional[str],
    user_id: Optional[int],
    on_done: Any = None,  # callable(report) -> dict[str, Any] 自定义 done 事件 payload
):
    """
    通用 SSE 生成器：start → 周期 heartbeat → done/error。
    on_done 给端点提供一次"把 report 转成端点希望的 done 事件 payload"的机会。
    """
    yield sse({"type": "started"})

    task = asyncio.create_task(
        run_sediment(trigger_text=trigger_text, session_key=session_key, user_id=user_id)
    )
    try:
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except asyncio.TimeoutError:
                yield sse({"type": "heartbeat"})
            except Exception:
                break

        try:
            outcome = task.result()
        except Exception as e:  # noqa: BLE001
            logger.exception("[sediment] 任务异常")
            yield sse({"type": "error", "message": str(e)[:500]})
            return

        if not outcome["ok"] and not outcome["report"].get("summary"):
            yield sse({"type": "error", "message": "沉淀失败：未拿到结构化结果"})
            return

        report = outcome["report"]
        payload = on_done(report) if on_done else {"result": report}
        yield sse({"type": "done", **payload})
    finally:
        if not task.done():
            task.cancel()
