"""
Artifact 工具：让 agent 把当前调用产出的文件或 chart 登记到 agent_artifact 表。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Annotated, Optional

from langchain.tools import tool
from loguru import logger

from app.models.standard.agent import AgentArtifact
from app.services.agent_runtime.call_context import get_agent_call_context

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _ensure_relative(path_str: str, workspace: Path) -> tuple[Path, Path]:
    """返回 (absolute_path, relative_to_project)；校验不越出 workspace。

    路径语义（与 deepagents virtual_mode 一致）：
      - "x/y.png"       → workspace/x/y.png（相对路径）
      - "/x/y.png"      → workspace/x/y.png（前导 / 视为 workspace 根，不是文件系统根）
      - "/large_tool_results/<id>.png" 是 deepagents 内部约定的虚拟路径，物理上落在 workspace 内
    """
    workspace = workspace.resolve()
    # 统一去掉前导 / ，把所有路径当作相对 workspace
    rel = path_str.lstrip("/").lstrip("\\")
    p = (workspace / rel).resolve()
    try:
        p.relative_to(workspace)
    except ValueError as e:
        raise ValueError(f"路径越出 workspace 范围：{p}") from e
    rel_to_project = p.relative_to(_PROJECT_ROOT.resolve())
    return p, rel_to_project


@tool
async def register_artifact(
        name: Annotated[str, "产物展示名，需带文件类型后缀，例：'标准去重报告.md'"],
        artifact_type: Annotated[str, "md / pdf / zip / xlsx / csv / json / image / chart / excalidraw / other"],
        relative_path: Annotated[Optional[str], "路径；file 类型必填，chart 类型留空"] = None,
        chart_spec: Annotated[
            Optional[str],
            "chart 类型时传 JSON 字符串，结构 {type: bar/line/pie/scatter, title, xField, yField, data:[...]}",
        ] = None,
        description: Annotated[Optional[str], "一句话描述"] = None,
) -> str:
    """
    把当前调用产出的文件或 chart 登记为可下载/渲染的 artifact。
    可下载文件：先把文件写入 workspace（write_file 或 shell），再调用本工具登记路径。
    chart：不写文件，直接把 chart_spec JSON 传进来即可。
    """
    ctx = get_agent_call_context()
    if ctx is None:
        return "注册失败：当前无 agent 调用上下文"
    workspace = ctx.workspace_dir or (_PROJECT_ROOT / ".agent_workspace")

    try:
        if artifact_type == "chart":
            if not chart_spec:
                return "chart 类型必须提供 chart_spec"
            try:
                spec = json.loads(chart_spec)
            except Exception as e:
                return f"chart_spec 不是合法 JSON：{e}"
            obj = await AgentArtifact.create(
                artifact_type="chart",
                name=name[:200],
                description=(description[:1000] if description else None),
                chart_spec=spec,
                message_id=ctx.message_id,
                session_id=ctx.session_id,
            )
            return f"已登记 chart artifact#{obj.id}"
        else:
            if not relative_path:
                return "file 类型必须提供 relative_path"
            abs_path, rel_to_project = _ensure_relative(relative_path, workspace)
            exists = await asyncio.to_thread(abs_path.is_file)
            if not exists:
                return f"文件不存在：{abs_path}"
            size = (await asyncio.to_thread(abs_path.stat)).st_size
            token = secrets.token_urlsafe(24)
            obj = await AgentArtifact.create(
                artifact_type=artifact_type[:16],
                name=name[:200],
                description=(description[:1000] if description else None),
                path=str(rel_to_project).replace("\\", "/"),
                size=size,
                download_token=token,
                message_id=ctx.message_id,
                session_id=ctx.session_id,
            )
            return (
                f"已登记 artifact#{obj.id}（{size} bytes）。"
                f"下载链接（工作流看板 fileNode 的 url 直接照抄它，勿自编 ID）："
                f"/ai/agent/artifacts/{obj.id}/download?inline=1"
            )
    except Exception as e:
        logger.exception("register_artifact 失败")
        return f"登记失败：{e}"


__all__ = ["register_artifact"]
