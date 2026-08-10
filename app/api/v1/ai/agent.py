"""
Agent 工作台 API

- 会话：/agent/sessions CRUD
- 消息：/agent/sessions/{session_key}/messages 拉取
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.expressions import Q

from app.core.ctx import CTX_USER_ID
from app.models.standard.agent import (
    AgentArtifact,
    AgentMessage,
    AgentSession,
)
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/agent", tags=["Agent 工作台"])

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent


# ── Helpers ────────────────────────────────────────────────────────────────────


def _gen_key(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _current_user_id() -> Optional[int]:
    uid = CTX_USER_ID.get()
    return uid or None


def _session_to_dict(s: AgentSession) -> dict[str, Any]:
    return {
        "sessionKey": s.session_key,
        "title": s.title,
        "threadId": s.thread_id,
        "messageCount": s.message_count,
        "isStarred": s.is_starred,
        "source": s.source,
        "workflowKey": s.workflow_key,
        "createdAt": int(s.create_time.timestamp() * 1000) if s.create_time else None,
        "updatedAt": int(s.update_time.timestamp() * 1000) if s.update_time else None,
    }


def _message_to_dict(m: AgentMessage) -> dict[str, Any]:
    d = {
        "id": m.id,
        "role": m.role,
        "content": m.content or "",
        "thinking": m.thinking or "",
        "toolSteps": m.tool_steps_json or [],
        "status": m.status,
        "error": m.error,
        "createdAt": int(m.create_time.timestamp() * 1000) if m.create_time else None,
    }
    if m.attachments_json:
        d["attachments"] = m.attachments_json
    return d


def _artifact_to_dict(a: AgentArtifact) -> dict[str, Any]:
    download_url = None
    if a.path:
        # 返回相对 VITE_SERVICE_BASE_URL 的路径，前端拿 baseURL 直接拼
        download_url = f"/ai/agent/artifacts/{a.id}/download"
    return {
        "id": a.id,
        "artifactType": a.artifact_type,
        "name": a.name,
        "description": a.description,
        "path": a.path,
        "size": a.size,
        "chartSpec": a.chart_spec,
        "messageId": a.message_id,
        "downloadUrl": download_url,
        "createdAt": int(a.create_time.timestamp() * 1000) if a.create_time else None,
    }


# ── Schemas ───────────────────────────────────────────────────────────────────


class SessionCreateReq(BaseModel):
    title: Optional[str] = Field(None, description="会话标题，缺省取'新对话'")


class SessionUpdateReq(BaseModel):
    title: Optional[str] = None
    is_starred: Optional[int] = None
    touch: Optional[bool] = Field(None, description="仅把会话顶到最近活跃（刷新 update_time），用于搜索定位后保持排序")


class MessageAppendReq(BaseModel):
    role: str = Field(..., description="user/assistant")
    content: Optional[str] = None
    thinking: Optional[str] = None
    tool_steps: Optional[list[dict]] = None
    status: str = "done"
    error: Optional[str] = None


# ── 会话 ──────────────────────────────────────────────────────────────────────


@router.get("/sessions", summary="会话列表")
async def list_sessions(
    limit: int = 100,
    keyword: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    workflow_key: Optional[str] = None,
):
    uid = _current_user_id()
    qs = AgentSession.filter(is_deleted=0)
    if uid is not None:
        qs = qs.filter(user_id=uid)
    if workflow_key:
        qs = qs.filter(workflow_key=workflow_key)
    if keyword:
        # 命中标题，或命中会话内任一消息内容（内容搜索限定在当前用户的会话范围内）
        msg_qs = AgentMessage.filter(content__icontains=keyword)
        if uid is not None:
            # 注意：__in 不能直接传 lazy 的 ValuesQuery（tortoise 会同步迭代它导致 TypeError），必须先 await 物化成列表
            my_session_ids = list(await AgentSession.filter(is_deleted=0, user_id=uid).values_list("id", flat=True))
            msg_qs = msg_qs.filter(session_id__in=my_session_ids)
        hit_session_ids = await msg_qs.distinct().values_list("session_id", flat=True)
        qs = qs.filter(Q(title__icontains=keyword) | Q(id__in=list(hit_session_ids)))
    if start_date:
        qs = qs.filter(create_time__gte=start_date)
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            qs = qs.filter(create_time__lt=end_dt)
        except ValueError:
            pass
    rows = await qs.order_by("-is_starred", "-update_time").limit(limit)
    return Success(data=[_session_to_dict(s) for s in rows])


@router.post("/sessions", summary="新建会话")
async def create_session(req: SessionCreateReq):
    uid = _current_user_id()
    key = _gen_key("sess")
    s = await AgentSession.create(
        session_key=key,
        user_id=uid,
        title=req.title or "新对话",
        thread_id=f"qa-{key}",
    )
    return Success(data=_session_to_dict(s))


@router.get("/sessions/{session_key}", summary="会话详情")
async def get_session(session_key: str):
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")
    return Success(data=_session_to_dict(s))


@router.patch("/sessions/{session_key}", summary="更新会话")
async def update_session(session_key: str, req: SessionUpdateReq):
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")
    if req.title is not None:
        s.title = req.title[:128]
    if req.is_starred is not None:
        s.is_starred = 1 if req.is_starred else 0
    await s.save()
    return Success(data=_session_to_dict(s))


@router.delete("/sessions/{session_key}", summary="删除会话（软删）")
async def delete_session(session_key: str):
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")
    s.is_deleted = 1
    await s.save()
    return Success(data=None, msg="已删除")


class SessionTruncateReq(BaseModel):
    truncate_from_message_id: int = Field(description="从此消息（含）开始删除，此消息及之后的全部消息和产物都会被清除")


@router.post("/sessions/{session_key}/truncate", summary="截断会话（删除指定消息及之后的全部内容）")
async def truncate_session(session_key: str, req: SessionTruncateReq):
    """删除 truncate_from_message_id（含）及之后的全部消息、批次子任务产物及物理文件，并回滚 LangGraph checkpointer。"""
    from langchain_core.messages import AIMessage, HumanMessage

    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")

    # 保留的消息（截断点之前）
    keep_msgs = await AgentMessage.filter(session_id=s.id, id__lt=req.truncate_from_message_id).order_by("id")

    # 要删除的消息（截断点含）
    del_msgs = await AgentMessage.filter(session_id=s.id, id__gte=req.truncate_from_message_id).order_by("id")

    if not del_msgs:
        return Success(data=_session_to_dict(s), msg="无需截断")

    del_msg_ids = [m.id for m in del_msgs]

    # 收集要删除的 artifacts（直接挂在消息上）
    artifacts_by_msg = await AgentArtifact.filter(message_id__in=del_msg_ids)

    all_artifacts = list(artifacts_by_msg)

    # 删除物理文件
    def _delete_files(artifact_list: list[AgentArtifact]) -> None:
        for a in artifact_list:
            if not a.path:
                continue
            fpath = _PROJECT_ROOT / a.path
            try:
                if fpath.exists():
                    fpath.unlink()
            except OSError:
                logger.warning(f"[truncate] 删除文件失败: {fpath}", exc_info=True)

    await asyncio.to_thread(_delete_files, all_artifacts)

    # 删除 DB 记录（artifact → message 顺序）
    if all_artifacts:
        await AgentArtifact.filter(id__in=[a.id for a in all_artifacts]).delete()
    await AgentMessage.filter(id__in=del_msg_ids).delete()

    # 更新会话 message_count
    s.message_count = len(keep_msgs)
    await s.save(update_fields=["message_count", "update_time"])

    # 回滚 LangGraph checkpointer：
    # messages channel 用 add_messages reducer——直接传重建消息（无 id）是**追加**而非替换，
    # 旧历史还在，保留的消息会重复叠在尾部。正确做法：同一次 aupdate_state 里先
    # RemoveMessage 清空 thread 现有全部消息，再写回保留消息（reducer 按列表顺序处理）。
    # 必须 await 同步完成：若放后台，用户截断后立刻继续对话会与旧上下文竞态。
    lc_messages = []
    for m in keep_msgs:
        if m.role == "user" and m.content:
            lc_messages.append(HumanMessage(content=m.content))
        elif m.role == "assistant" and m.content:
            lc_messages.append(AIMessage(content=m.content))

    try:
        from langchain_core.messages import RemoveMessage

        from app.api.v1.ai.qa import _get_agent_for_session

        uid = _current_user_id()
        agent = await _get_agent_for_session(session_key, uid)
        config = {
            "configurable": {
                "thread_id": s.thread_id,
                "user_id": str(uid) if uid else "0",
            }
        }
        current = await agent.aget_state(config)
        existing = (current.values.get("messages") or []) if current and current.values else []
        removals = [RemoveMessage(id=m.id) for m in existing if getattr(m, "id", None)]
        await agent.aupdate_state(config, {"messages": removals + lc_messages})
    except Exception:
        logger.warning("[truncate] checkpointer 回滚失败，agent 上下文可能与界面不一致", exc_info=True)

    return Success(data=_session_to_dict(s), msg=f"已删除 {len(del_msgs)} 条消息及相关产物")


# ── 消息 ──────────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_key}/messages", summary="会话消息")
async def list_messages(session_key: str):
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")
    rows = await AgentMessage.filter(session_id=s.id).order_by("id")

    # Artifacts：按 message_id 预取
    message_ids = [m.id for m in rows]
    artifacts_by_msg: dict[int, list[AgentArtifact]] = {}
    if message_ids:
        for a in await AgentArtifact.filter(message_id__in=message_ids).order_by("id"):
            artifacts_by_msg.setdefault(a.message_id, []).append(a)

    result = []
    for m in rows:
        d = _message_to_dict(m)
        d["artifacts"] = [_artifact_to_dict(a) for a in artifacts_by_msg.get(m.id, [])]
        result.append(d)
    return Success(data=result)


@router.post("/sessions/{session_key}/messages", summary="追加消息")
async def append_message(session_key: str, req: MessageAppendReq):
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None:
        return Fail(msg="会话不存在")

    m = await AgentMessage.create(
        session_id=s.id,
        role=req.role,
        content=req.content,
        thinking=req.thinking,
        tool_steps_json=req.tool_steps,
        status=req.status,
        error=req.error,
    )
    # 维护会话 message_count / updated_at
    s.message_count = s.message_count + 1
    if req.role == "user" and (not s.title or s.title == "新对话") and (req.content or "").strip():
        s.title = (req.content or "").strip()[:36]
    await s.save()
    return Success(data=_message_to_dict(m))


# ── Artifacts ────────────────────────────────────────────────────────────────


@router.get("/artifacts", summary="产物列表")
async def list_artifacts(
    message_id: Optional[int] = None,
    session_id: Optional[int] = None,
    limit: int = 200,
):
    qs = AgentArtifact.all()
    if message_id is not None:
        qs = qs.filter(message_id=message_id)
    if session_id is not None:
        qs = qs.filter(session_id=session_id)
    rows = await qs.order_by("id").limit(limit)
    return Success(data=[_artifact_to_dict(a) for a in rows])


# ── Excalidraw 编辑回写 ──────────────────────────────────────────────────────


class ExcalidrawSaveIn(BaseModel):
    sceneJson: str = Field(..., description="新的 .excalidraw JSON 字符串")


@router.put("/artifacts/{artifact_id}/excalidraw", summary="覆盖 excalidraw 源并重生成 SVG")
async def save_excalidraw(artifact_id: int, payload: ExcalidrawSaveIn):
    a = await AgentArtifact.get_or_none(id=artifact_id)
    if a is None or a.artifact_type != "excalidraw" or not a.path:
        return Fail(code="4040", msg="excalidraw 产物不存在")

    try:
        scene = json.loads(payload.sceneJson)
    except Exception as e:
        return Fail(msg=f"sceneJson 不是合法 JSON：{e}")
    if not isinstance(scene, dict) or scene.get("type") != "excalidraw":
        return Fail(msg="sceneJson 缺少 type=excalidraw 字段")

    project_root = Path(__file__).parent.parent.parent.parent.parent.resolve()
    workspace_root = (project_root / ".agent_workspace").resolve()
    excal_abs = (project_root / a.path).resolve()
    try:
        excal_abs.relative_to(workspace_root)
    except ValueError:
        return Fail(code="4030", msg="路径越权")

    pretty = json.dumps(scene, ensure_ascii=False, indent=2)
    await asyncio.to_thread(excal_abs.write_text, pretty, "utf-8")
    new_size = (await asyncio.to_thread(excal_abs.stat)).st_size
    a.size = new_size

    # 同 message 下找配对 SVG（artifactType=image 且 name 同名 .svg），重新生成
    svg_artifact: Optional[AgentArtifact] = None
    if a.message_id is not None:
        peers = await AgentArtifact.filter(message_id=a.message_id).all()
        stem = excal_abs.stem
        for p in peers:
            if p.id == a.id or not p.path:
                continue
            p_abs = (project_root / p.path).resolve()
            if p_abs.suffix.lower() == ".svg" and p_abs.stem == stem:
                svg_artifact = p
                break

    converter = workspace_root / ".agent_skills" / "excalidraw-diagram-generator" / "scripts" / "excalidraw-to-svg.py"
    svg_info: dict[str, Any] = {}
    if svg_artifact and converter.is_file():
        svg_abs = (project_root / svg_artifact.path).resolve()  # type: ignore[arg-type]
        try:
            svg_abs.relative_to(workspace_root)
        except ValueError:
            return Fail(code="4030", msg="SVG 路径越权")

        proc = await asyncio.create_subprocess_exec(
            "python",
            str(converter),
            str(excal_abs),
            str(svg_abs),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_root),
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("excalidraw-to-svg 失败：{}", stderr.decode("utf-8", "ignore"))
            return Fail(msg="SVG 重生成失败，已写入 .excalidraw 但 SVG 未更新")
        new_svg_size = (await asyncio.to_thread(svg_abs.stat)).st_size
        svg_artifact.size = new_svg_size
        await svg_artifact.save()
        svg_info = {"id": svg_artifact.id, "size": new_svg_size}

    await a.save()
    return Success(data={"id": a.id, "size": a.size, "svg": svg_info or None})
