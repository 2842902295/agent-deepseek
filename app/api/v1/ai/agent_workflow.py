"""
Agent 共享工作流 API

人和 Agent 共读共写的工作流（Vue Flow JSON），支持：
- 创建 / 读取（全量或部分节点）/ 整体更新 / 部分合并节点 / 软删除
- 按用户列表查询

板型（board_type）：
- board：节点连线流程板（默认），nodes/edges 存 Vue Flow JSON
- html：HTML 看板——agent 像开发者一样在任务目录（users/{uid}/apps/{workflow_key}/）开发
  多文件 HTML 应用，前端 iframe 画布渲染；nodes/edges 恒为空，入口 index.html 存在与否即 entryReady
"""

import asyncio
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from tortoise import timezone as tz

from app.core.ctx import CTX_USER_ID
from app.core.dependency import AuthControl
from app.models.standard.agent import AgentWorkflow
from app.schemas.base import Fail, Success
from app.utils.security import create_html_app_token

router = APIRouter(prefix="/agent-workflows", tags=["AI-共享工作流"])

# HTML 看板任务的应用目录根（与 qa.py / agent_public.py 同款约定：项目根/.agent_workspace）
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_USERS_ROOT = _PROJECT_ROOT / ".agent_workspace" / "users"

# 合法板型
_BOARD_TYPES = {"board", "html"}


def _app_dir(user_id: Any, workflow_key: str) -> Path:
    """HTML 看板任务的应用目录：users/{uid}/apps/{workflow_key}/（也是 agent 文件工具眼里的 apps/{wk}/）"""
    return _USERS_ROOT / str(user_id) / "apps" / workflow_key


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    title: str = Field("未命名工作流", alias="title")
    session_key: Optional[str] = Field(None, alias="sessionKey")
    # 板型：board 流程板（默认）/ html HTML看板
    board_type: str = Field("board", alias="boardType")
    nodes: Optional[List[Any]] = None
    edges: Optional[List[Any]] = None
    viewport: Optional[dict] = None

    class Config:
        populate_by_name = True


class WorkflowUpdate(BaseModel):
    title: Optional[str] = None
    nodes: Optional[List[Any]] = None
    edges: Optional[List[Any]] = None
    viewport: Optional[dict] = None
    # 人本次改动的简报（前端本地 diff 后带上），供 Agent 下轮读板时感知人的改动
    human_edit: Optional[dict] = Field(None, alias="humanEdit")
    # 节点徽标（临时协作态）：前端维护人编辑标，随保存原样回传；agent 端写入时全量重建
    marks: Optional[dict] = None

    class Config:
        populate_by_name = True


class NodesPatch(BaseModel):
    nodes: List[Any]
    human_edit: Optional[dict] = Field(None, alias="humanEdit")

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gen_key() -> str:
    return "wf_" + secrets.token_hex(12)


async def _get_owned(workflow_key: str) -> Optional[AgentWorkflow]:
    """按 workflow_key 取当前用户拥有的未删除工作流。"""
    uid = CTX_USER_ID.get()
    return await AgentWorkflow.get_or_none(
        workflow_key=workflow_key, user_id=uid, is_deleted=0
    )


async def _to_dict(wf: AgentWorkflow) -> dict:
    d = await wf.to_dict()
    d["workflowKey"] = wf.workflow_key
    d["sessionKey"] = wf.session_key
    d["boardType"] = wf.board_type or "board"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", summary="创建工作流")
async def create_workflow(
        body: WorkflowCreate,
        _auth: AuthControl = Depends(),
):
    uid = CTX_USER_ID.get()
    board_type = body.board_type or "board"
    if board_type not in _BOARD_TYPES:
        return Fail(code="4000", msg="未知板型")
    wf = await AgentWorkflow.create(
        workflow_key=_gen_key(),
        session_key=body.session_key,
        user_id=uid,
        title=body.title[:128],
        board_type=board_type,
        nodes=body.nodes or [],
        edges=body.edges or [],
        viewport=body.viewport,
        version=1,
        editor="human",
    )
    # HTML 看板：预建应用目录（agent 文件工具与托管路由的共同落点）
    if board_type == "html":
        await asyncio.to_thread(_app_dir(uid, wf.workflow_key).mkdir, parents=True, exist_ok=True)
    return Success(data=await _to_dict(wf), msg="创建成功")


@router.get("/list", summary="列出当前用户的工作流")
async def list_workflows(
        keyword: Optional[str] = None,
        _auth: AuthControl = Depends(),
):
    uid = CTX_USER_ID.get()
    # 空板兜底清扫：列表接口先清后查——0 节点且超过 20 分钟无更新的板软删掉再返回。
    # 前端只在 goBack / 组件卸载时即时清理，关标签页、离线等路径会漏；放在列表请求里兜底，
    # 无论空板是怎么剩下的，打开列表时必然已经清掉（同请求内先删后查，不存在"删慢了列表还能看到"的时序问题）。
    # 20 分钟宽限：保护刚建好、agent 还没来得及播种的板（用户可能正在板子页等首条回复）。
    # 豁免 html 看板：它的 nodes 恒为空，"有没有内容"看的是目录里的 index.html（前端 goBack/unmount
    # 用 entryReady 即时清理；列表级文件判空成本高，v1 直接豁免）。
    cutoff = tz.now() - timedelta(minutes=20)
    stale_ids = [
        wf.id
        async for wf in AgentWorkflow.filter(
            user_id=uid, is_deleted=0, update_time__lt=cutoff
        ).only("id", "nodes", "board_type")
        if not (wf.nodes or []) and (wf.board_type or "board") != "html"
    ]
    if stale_ids:
        await AgentWorkflow.filter(id__in=stale_ids).update(is_deleted=1)
    qs = AgentWorkflow.filter(user_id=uid, is_deleted=0)
    if keyword:
        qs = qs.filter(title__icontains=keyword)
    rows = await qs.order_by("-update_time").limit(50)

    def _ms(v) -> Optional[int]:
        return int(v.timestamp() * 1000) if v else None

    def _node_label(n: dict) -> str:
        """列表卡片预览用的节点短标签（按节点类型取字段）。"""
        nd = n.get("data") or {}
        ntype = str(n.get("type") or "textNode")
        if ntype == "fileNode":
            return f"📎 {nd.get('name') or '附件'}"
        if ntype == "segNode":  # 品牌板型：分镜段卡（generic，一卡一场戏）
            head = " ".join(x for x in [str(nd.get("seg") or ""), str(nd.get("duration") or "")] if x)
            shots = nd.get("shots")
            shots_s = f"（分镜 {len(shots)}）" if isinstance(shots, list) and shots else ""
            return f"🎬 {head or '分镜段'}{shots_s}"
        if ntype == "reviewNode":
            return f"✋ {nd.get('question') or '人工核查'}"
        if ntype == "taskNode":
            return str(nd.get("title") or "工作项")
        if ntype == "dataNode":
            return str(nd.get("title") or "数据")
        if ntype == "conclusionNode":
            return f"◆ {nd.get('claim') or '结论'}"
        if ntype in ("startNode", "endNode"):
            return str(nd.get("label") or ("开始" if ntype == "startNode" else "结束"))
        return str(nd.get("text") or "未命名")

    data = []
    for r in rows:
        node_list = r.nodes or []
        # 前 4 个节点的精简预览（供列表卡片画迷你流程），label 截断防载荷膨胀
        preview = [
            {
                "label": _node_label(n)[:20],
                "type": str(n.get("type") or "textNode"),
            }
            for n in node_list[:4]
        ]
        data.append({
            "workflowKey": r.workflow_key,
            "title": r.title,
            "boardType": r.board_type or "board",
            "version": r.version,
            "updateTime": _ms(r.update_time),
            "nodeCount": len(node_list),
            "edgeCount": len(r.edges or []),
            "preview": preview,
        })
    return Success(data=data)


@router.get("/{workflow_key}", summary="读取工作流")
async def get_workflow(
        workflow_key: str,
        node_ids: Optional[str] = None,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    d = await _to_dict(wf)

    # HTML 看板：附带入口就绪状态（index.html 是否已发布），前端画布据此决定渲染 iframe 还是占位
    if (wf.board_type or "board") == "html":
        uid = CTX_USER_ID.get()
        d["entryReady"] = await asyncio.to_thread((_app_dir(uid, workflow_key) / "index.html").exists)

    # 部分读取：?node_ids=1,2,3
    if node_ids and wf.nodes:
        wanted = set(node_ids.split(","))
        d["nodes"] = [n for n in wf.nodes if str(n.get("id")) in wanted]
        # 附带关联边
        if wf.edges:
            d["edges"] = [
                e for e in wf.edges
                if str(e.get("source")) in wanted or str(e.get("target")) in wanted
            ]

    return Success(data=d)


@router.put("/{workflow_key}", summary="整体更新工作流")
async def update_workflow(
        workflow_key: str,
        body: WorkflowUpdate,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    payload = body.model_dump(exclude_unset=True, by_alias=False)
    human_edit = payload.pop("human_edit", None)
    if payload or human_edit is not None:
        if payload:
            await wf.update_from_dict(payload).save()
        wf.editor = "human"
        if human_edit is not None:
            wf.human_edit = human_edit
        wf.version += 1
        await wf.save()

    return Success(data=await _to_dict(wf), msg="更新成功")


@router.patch("/{workflow_key}/nodes", summary="部分合并节点（按 ID）")
async def patch_nodes(
        workflow_key: str,
        body: NodesPatch,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    existing: list = wf.nodes or []
    idx = {str(n.get("id")): i for i, n in enumerate(existing)}

    for incoming in body.nodes:
        nid = str(incoming.get("id", ""))
        if not nid:
            continue
        if nid in idx:
            # 深度合并 data 字段，其余字段覆盖
            old = existing[idx[nid]]
            for k, v in incoming.items():
                if k == "data" and isinstance(v, dict) and isinstance(old.get("data"), dict):
                    old["data"].update(v)
                else:
                    old[k] = v
        else:
            existing.append(incoming)

    wf.nodes = existing
    wf.editor = "human"
    if body.human_edit is not None:
        wf.human_edit = body.human_edit
    wf.version += 1
    await wf.save()

    return Success(data=await _to_dict(wf), msg="节点已合并")


@router.delete("/{workflow_key}", summary="删除工作流（软删）")
async def delete_workflow(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    wf.is_deleted = 1
    await wf.save()
    # HTML 看板：连带物理删除应用目录（DB 软删为准，rmtree 失败不回滚）。
    # 目录消失后，已签发的托管 token 也自然全部 404（无状态 token 的撤销兜底）。
    if (wf.board_type or "board") == "html":
        uid = CTX_USER_ID.get()
        await asyncio.to_thread(shutil.rmtree, _app_dir(uid, workflow_key), True)
    return Success(msg="已删除")


@router.post("/{workflow_key}/html-token", summary="签发 HTML 看板托管 token")
async def sign_html_token(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    """签发短期托管 token：iframe 以 /ai/html-app/{token}/index.html 为 src，
    页面内相对引用的 css/js/json 与 save 写回都走同一 token 前缀（iframe 无法带 Authorization 头）。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    if (wf.board_type or "board") != "html":
        return Fail(code="4000", msg="非 HTML 看板任务")

    uid = CTX_USER_ID.get()
    ttl_days = 7
    token = create_html_app_token(int(uid), workflow_key, ttl_days=ttl_days)
    entry_ready = await asyncio.to_thread((_app_dir(uid, workflow_key) / "index.html").exists)
    return Success(data={"token": token, "entryReady": entry_ready, "expiresIn": ttl_days * 86400})
