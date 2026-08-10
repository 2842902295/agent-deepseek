"""
Agent 工作台公开路由（无需鉴权）。

目前包含：
- 产物下载 /agent/artifacts/{id}/download
- 用户上传文件下载 /agent/uploads/download
- 应用配置 /agent/app-config（品牌变体等前端初始化配置）

下载本质就是静态资源，做成和图片一样公开访问即可；路径校验保证不越权。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.models.standard.agent import AgentArtifact
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/agent", tags=["Agent 工作台（公开）"])

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"
_USERS_ROOT = _WORKSPACE / "users"


@router.get("/artifacts/{artifact_id}/download", summary="下载产物（公开）")
async def download_artifact(artifact_id: int, inline: int = 0):
    a = await AgentArtifact.get_or_none(id=artifact_id)
    if a is None or not a.path:
        return Fail(code="4040", msg="产物不存在")
    abs_path = (_PROJECT_ROOT / a.path).resolve()
    workspace_root = _WORKSPACE.resolve()
    try:
        abs_path.relative_to(workspace_root)
    except ValueError:
        return Fail(code="4030", msg="路径越权")
    if not abs_path.exists() or not abs_path.is_file():
        return Fail(code="4040", msg="文件已不存在")
    # inline=1：不下发 Content-Disposition: attachment，让 <video>/<audio> 等
    # 标签能正常发 Range 请求做流式播放与拖动；下载链接保持默认 attachment 行为。
    media_type, _ = mimetypes.guess_type(abs_path.name)
    if inline:
        return FileResponse(
            str(abs_path),
            media_type=media_type or "application/octet-stream",
        )
    return FileResponse(str(abs_path), filename=a.name)


@router.get("/uploads/download", summary="下载用户上传文件（公开，用于前端回显）")
async def download_upload(path: str, user_id: int | None = None):
    if not path.strip():
        return Fail(msg="path 不能为空")

    safe_path = Path(path.strip())
    if ".." in safe_path.parts or safe_path.is_absolute():
        return Fail(code="4030", msg="非法路径")

    base = _USERS_ROOT / str(user_id) if user_id else _WORKSPACE
    full_path = (base / safe_path).resolve()

    workspace_root = _WORKSPACE.resolve()
    try:
        full_path.relative_to(workspace_root)
    except ValueError:
        return Fail(code="4030", msg="路径越权")

    if not full_path.exists() or not full_path.is_file():
        return Fail(code="4040", msg="文件不存在")

    media_type, _ = mimetypes.guess_type(full_path.name)
    return FileResponse(
        str(full_path),
        filename=full_path.name,
        media_type=media_type or "application/octet-stream",
    )


@router.get("/app-config", summary="获取前端初始化配置（无需鉴权）")
async def get_app_config():
    from app.settings import APP_SETTINGS
    return Success(data={"brand_variant": APP_SETTINGS.BRAND_VARIANT.lower()})
