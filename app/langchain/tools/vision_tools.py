"""
视觉理解工具

主对话模型不支持图片/视频时，agent 通过 vision_inspect 调用 VISION 角色看图/看视频
（图片走 base64 data URL，视频发布为公网 URL 后 video_url 直传）。
未配置 VISION_* 时，工具不会被注入到 agent。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Annotated

from langchain.tools import tool
from langchain_core.messages import HumanMessage
from loguru import logger

from app.langchain.config import has_role
from app.langchain.llm_providers import get_vision_llm
from app.services.agent_runtime.call_context import get_agent_call_context

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"

# 单图大小上限（字节），超过会让 PIL 缩到长边 2048
_MAX_BYTES = 8 * 1024 * 1024
_MAX_LONG_EDGE = 2048


async def _resolve_image(path_str: str) -> tuple[bytes, str]:
    """
    解析图片为 (bytes, mime_type)。支持：
      - http(s)://...                 直接下载（异步 httpx）
      - 工作区相对路径 / 前导 / 的虚拟路径
      - 文件系统绝对路径（仅当 ctx 缺失或调用方明确传绝对路径时）
    """
    if path_str.startswith(("http://", "https://")):
        import httpx
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(path_str)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/png").split(";")[0].strip()
            return r.content, mime

    ctx = get_agent_call_context()
    workspace = (ctx.workspace_dir if ctx and ctx.workspace_dir else _DEFAULT_WORKSPACE).resolve()

    p = Path(path_str)
    if p.is_absolute():
        target = p.resolve()
    else:
        target = (workspace / path_str.lstrip("/").lstrip("\\")).resolve()

    if not await asyncio.to_thread(target.exists):
        raise FileNotFoundError(f"文件不存在：{path_str}")

    data = await asyncio.to_thread(target.read_bytes)
    mime = mimetypes.guess_type(target.name)[0] or "image/png"
    return data, mime


def _maybe_resize_sync(data: bytes, mime: str) -> tuple[bytes, str]:
    """超过 _MAX_BYTES 或长边超 _MAX_LONG_EDGE 时缩放，避免 413。CPU 密集，调方需放线程池。"""
    if len(data) <= _MAX_BYTES:
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as img:
                if max(img.size) <= _MAX_LONG_EDGE:
                    return data, mime
        except Exception:
            return data, mime

    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            scale = _MAX_LONG_EDGE / max(img.size)
            if scale < 1:
                new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                img = img.resize(new_size)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning(f"图片缩放失败，使用原图：{e}")
        return data, mime


def _to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# 视频大小上限（字节），超过提示先裁剪
_MAX_VIDEO_BYTES = 100 * 1024 * 1024


@tool
async def vision_inspect(
        file: Annotated[str, "图片/视频的工作区相对路径（如 'shot.png'、'out/demo.mp4'）或 http(s) URL"],
        question: Annotated[
            str,
            "对图片/视频提问，越具体越好。如：'提取表格里的数据并以 markdown 输出'、'描述视频里的画面'",
        ] = "请详细描述这个文件的内容。",
) -> str:
    """
    让视觉模型查看图片或视频并回答问题。当主模型无法直接读懂图片/视频时使用。
    支持工作区相对路径或 http(s) URL；图片过大会自动缩放，视频发布为公网 URL
    后交给视觉模型原生理解（只把文字回答返回给主 agent，不占主对话上下文）。
    """
    if not has_role("VISION"):
        return "❌ 视觉模型未配置（.env 缺少 VISION_BASE_URL/API_KEY/MODEL）"

    try:
        data, mime = await _resolve_image(file)
    except FileNotFoundError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ 文件读取失败：{e}"

    is_video = mime.startswith("video/")
    if is_video:
        if len(data) > _MAX_VIDEO_BYTES:
            return f"❌ 视频过大（{len(data) // 1048576}MB > 100MB），请先用 shell 裁剪后再查看"
        from app.api.v1.ai.upload import publish_conversation_media

        ext = "" if file.startswith(("http://", "https://")) else Path(file).suffix.lower()
        url = await publish_conversation_media(data, mime, ext=ext)
        if not url:
            return "❌ 视频发布公网 URL 失败（未配置 PUBLIC_BASE_URL 或上传失败），无法查看"
        media_block = {"type": "video_url", "video_url": {"url": url}}
    else:
        # PIL 缩放是 CPU 密集，必须丢线程池
        data, mime = await asyncio.to_thread(_maybe_resize_sync, data, mime)
        media_block = {"type": "image_url", "image_url": {"url": _to_data_url(data, mime)}}

    llm = get_vision_llm()
    if llm is None:
        return "❌ 视觉模型未配置"

    msg = HumanMessage(content=[
        {"type": "text", "text": question},
        media_block,
    ])

    try:
        resp = await llm.ainvoke([msg])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return content or "（视觉模型返回为空）"
    except Exception as e:
        logger.exception("vision_inspect 调用失败")
        return f"❌ 视觉模型调用失败：{e}"



__all__ = ["vision_inspect"]
