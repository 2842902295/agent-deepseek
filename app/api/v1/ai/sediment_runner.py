"""
沉淀运行时：所有沉淀类端点共用的"调 qa_agent + 跑 sediment skill"逻辑。

设计（dsh 内核版）：
- 复用按用户缓存的 qa_agent 实例；沉淀跑在**全新临时会话**里（不污染主对话）
- dsh 会话记忆只活在运行时进程内、无法跨会话语义复用，`aget_state` 对 dsh 恒返回空——
  因此主对话素材改由本模块从 DB（agent_message）重建 transcript，**拼进 trigger
  消息自包含**，agent 无需外部记忆即可整理
- agent 通过 SkillsMiddleware / 触发文本里的内联指令执行沉淀工作流
- agent 输出末尾必须有 <sediment-report>{...}</sediment-report> marker；从 marker 抠 JSON 反给前端
- marker 抓不到时，从工具调用历史里反查（skill_save 的返回文本）做兜底
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from typing import Any, Optional

from loguru import logger

from app.api.v1.ai.qa import _get_agent_for_session

# 为不需要绑定具体会话的入口（万用收件箱）使用的"匿名沉淀"工作区前缀
_ANON_SESSION_PREFIX = "sed_anon_"
_REPORT_MARKER = re.compile(r"<sediment-report>\s*(\{.*?\})\s*</sediment-report>", re.DOTALL)

# 沉淀素材上限（区别于回合续接 priming：沉淀的目的就是回看对话，额度更大；env 可调）
_SEDIMENT_MAX_MESSAGES = int(os.environ.get("SEDIMENT_MAX_MESSAGES", "60"))
_SEDIMENT_PER_MSG_CHARS = int(os.environ.get("SEDIMENT_PER_MSG_CHARS", "3000"))
_SEDIMENT_TOTAL_CHARS = int(os.environ.get("SEDIMENT_TOTAL_CHARS", "48000"))


async def _load_session_transcript(session_key: str) -> str:
    """从 agent_message 表重建近期对话文本（时间正序），作为沉淀回合的素材。

    dsh 迁移后主对话记忆无法被沉淀回合复用（临时会话是运行时侧全新空会话），
    DB 是唯一可靠历史源。失败返回空串（降级为无素材，由调用方决定是否继续）。
    """
    try:
        from app.models.standard.agent import AgentMessage, AgentSession

        s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
        if s is None:
            return ""
        rows = await AgentMessage.filter(session_id=s.id).order_by("-id").limit(_SEDIMENT_MAX_MESSAGES)
        rows = list(reversed(rows))
        # 剔除尾部未完成的 streaming 占位（并发边界下可能存在）
        while rows and rows[-1].role == "assistant" and rows[-1].status == "streaming":
            rows.pop()
        lines: list[str] = []
        total = 0
        truncated = False
        for m in rows:
            content = (m.content or "").strip()
            if not content:
                continue
            if len(content) > _SEDIMENT_PER_MSG_CHARS:
                content = content[: _SEDIMENT_PER_MSG_CHARS] + "…（截断）"
            role = "用户" if m.role == "user" else "助手"
            if m.role == "assistant" and m.status == "aborted":
                content += "（此条回复被用户中断）"
            line = f"{role}: {content}"
            if total + len(line) > _SEDIMENT_TOTAL_CHARS:
                truncated = True
                break
            lines.append(line)
            total += len(line)
        if truncated:
            lines.insert(0, "…（更早的历史已省略）")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 - 重建失败降级为无素材，不阻断沉淀
        logger.warning(f"[sediment] 重建对话历史失败（降级为无素材）: {e}")
        return ""


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
      沉淀跑在一个**全新的临时会话**上（临时 thread_id，与主会话不相干）。
      dsh 运行时侧会话记忆无法跨会话语义复用，主对话素材由本函数从 DB
      重建后拼进触发消息——单条自包含消息即完整输入。
    """
    if session_key:
        sk = session_key
    else:
        sk = f"{_ANON_SESSION_PREFIX}{secrets.token_hex(4)}"

    agent = await _get_agent_for_session(sk, user_id)

    # 素材拼装：主对话 transcript 附在触发指令之后（自包含）。
    # 匿名入口（收件箱）素材本就在 trigger 里，跳过。
    text = trigger_text
    if session_key:
        transcript = await _load_session_transcript(session_key)
        if transcript:
            text = (
                f"{trigger_text}\n\n"
                "─── 以下是需要处理的对话历史 ───\n"
                f"{transcript}\n"
                "─── 对话历史结束 ───"
            )
        else:
            logger.warning(f"[sediment] 未重建到对话历史 session={session_key}，沉淀将无素材执行")

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

    # dsh HTTP 桥：登记沉淀回合上下文（无消息关联，仅工作区；skill_save 等工具需要）；
    # token 持有至收尾，清理只删自己的条目
    _turn_token = None
    try:
        from app.api.v1.ai.qa import _user_workspace
        from app.mcp_bridge.dsh_http_bridge import set_active_turn

        _turn_token = set_active_turn(user_id, session_key=sk, workspace=_user_workspace(user_id))
    except Exception:  # noqa: BLE001
        pass

    try:
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": text}]}, config=config
            )
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
        try:
            from app.mcp_bridge.dsh_http_bridge import clear_active_turn

            clear_active_turn(user_id, _turn_token)
        except Exception:  # noqa: BLE001
            pass
        # 注：临时会话的 JSONL 档案留档在 $DSH_HOME/sessions（dsh 无 checkpointer 可清），
        # 单回合档案很小，暂不主动清理


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
