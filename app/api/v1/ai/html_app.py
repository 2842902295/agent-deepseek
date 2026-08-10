"""
HTML 看板应用托管（公开路由，token 门控）

深度任务「HTML 看板型」：agent 在 users/{uid}/apps/{workflow_key}/ 目录开发多文件 HTML 应用，
前端 iframe 以 `/api/v1/ai/html-app/{token}/index.html` 为 src 渲染。

为什么走 URL token 而不是 Authorization 头：
- iframe 的 src 请求无法自定义请求头；页面内相对引用的 css/js/子页面与 fetch('./data.json') 也都不带头。
- 因此 token 内嵌在 URL 路径里，所有相对引用自动解析到同一 token 前缀下（token 即凭据）。

token 语义（app/utils/security.py::create_html_app_token）：
- HS256 无状态签名，TTL 7d，claims = {tokenType: "htmlAppToken", userId, workflowKey}
- tokenType 与登录态双向隔离：dependency.py 硬校验 tokenType == "accessToken"，本 token 打任何
  鉴权接口都被拒；反向本路由只认 htmlAppToken，登录 token 也不能当托管 token 用。
- 权限域 = 单个任务目录的「读任意文件 + 写 json」；删任务即删目录，已签发 token 自然全部 404。

红线（对照 CLAUDE.md）：
- 公开端点绝不使用 4001/4002/4003/4010 鉴权契约码（那是 axios 拦截器的登出/刷新信号）；
  GET（iframe 消费）失败返回 PlainTextResponse 404，POST save（页面 JS 消费）失败用 Fail 4000 系。
- async 端点内所有磁盘 I/O 走 asyncio.to_thread。
- token 会出现在服务端访问日志（URL 内嵌的固有代价，内网管理系统可接受）；
  Referer 泄漏由 iframe referrerpolicy + 页面 meta referrer no-referrer 兜住。
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from app.schemas.base import Fail, Success
from app.utils.security import decode_html_app_token

router = APIRouter(prefix="/html-app", tags=["AI-HTML看板（公开）"])

# workspace 根（与 agent_public.py / qa.py 同款约定）
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"
_USERS_ROOT = _WORKSPACE / "users"

# 单文件写回上限（json 数据层定位，防把应用中转站当网盘）
_SAVE_MAX_BYTES = 2 * 1024 * 1024

# 自带 MIME 映射（不依赖 mimetypes：Windows 注册表可能污染/缺失映射，中文文本类型必须带 charset）
_EXT_MIME = {
    ".html": "text/html", ".htm": "text/html",
    ".js": "text/javascript", ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".txt": "text/plain", ".md": "text/plain", ".csv": "text/csv",
    ".wasm": "application/wasm", ".pdf": "application/pdf",
}
_TEXT_MIME = {"text/html", "text/javascript", "text/css", "application/json", "text/plain", "text/csv"}

_NO_STORE = {"Cache-Control": "no-store"}


def _app_base(user_id: int, workflow_key: str) -> Path:
    return _USERS_ROOT / str(user_id) / "apps" / workflow_key


def _resolve_inside(base: Path, rel: str) -> Optional[Path]:
    """把相对路径解析到 base 内；任何越权/非法形态返回 None。

    三重拒绝 + 双 relative_to 保险：`..` 段、反斜杠（%5C）、绝对路径/盘符先拒；
    再 resolve 后分别对 base 与 users 根做 relative_to，symlink 逃逸也兜住。
    """
    if not rel or "\\" in rel or rel.startswith("/") or ":" in rel.split("/")[0]:
        return None
    parts = rel.split("/")
    if ".." in parts or "." in parts:
        return None
    try:
        target = (base / rel).resolve()
        target.relative_to(base.resolve())
        target.relative_to(_USERS_ROOT.resolve())
    except (ValueError, OSError):
        return None
    return target


def _media_type(path: Path) -> str:
    mt = _EXT_MIME.get(path.suffix.lower(), "application/octet-stream")
    if mt in _TEXT_MIME:
        mt += "; charset=utf-8"
    return mt


def _write_atomic(target: Path, text: str) -> None:
    """同目录 tmp + os.replace 原子替换（Windows NTFS 上同样原子，避免 iframe 读到半截 JSON）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


@router.get("/{token}/{file_path:path}", summary="托管 HTML 看板应用静态文件（token 门控）")
async def serve_file(token: str, file_path: str):
    claims = decode_html_app_token(token)
    if claims is None:
        return PlainTextResponse("链接无效或已过期", status_code=404)

    # 目录形态兜底：/ 或 pages/ 结尾 → index.html
    if not file_path:
        file_path = "index.html"
    elif file_path.endswith("/"):
        file_path += "index.html"

    base = _app_base(int(claims["userId"]), str(claims["workflowKey"]))
    target = _resolve_inside(base, file_path)
    if target is None:
        return PlainTextResponse("链接无效或已过期", status_code=404)

    exists = await asyncio.to_thread(target.is_file)
    if not exists:
        return PlainTextResponse("文件不存在", status_code=404)

    return FileResponse(target, media_type=_media_type(target), headers=_NO_STORE)


class HtmlAppSave(BaseModel):
    path: str
    data: Any

    class Config:
        populate_by_name = True


@router.post("/{token}/save", summary="页面内写回 json 数据（token 门控）")
async def save_json(token: str, body: HtmlAppSave):
    """应用页面的数据持久化通道：仅允许任务目录内的 .json 文件，服务端 dumps 保证合法 JSON。"""
    claims = decode_html_app_token(token)
    if claims is None:
        return Fail(code="4030", msg="链接无效或已过期")

    rel = (body.path or "").strip()
    if not rel.lower().endswith(".json"):
        return Fail(code="4000", msg="仅支持保存 .json 数据文件")

    base = _app_base(int(claims["userId"]), str(claims["workflowKey"]))
    target = _resolve_inside(base, rel)
    if target is None:
        return Fail(code="4000", msg="非法路径")

    text = json.dumps(body.data, ensure_ascii=False)
    if len(text.encode("utf-8")) > _SAVE_MAX_BYTES:
        return Fail(code="4000", msg="数据过大")

    await asyncio.to_thread(_write_atomic, target, text)
    return Success(msg="已保存")
