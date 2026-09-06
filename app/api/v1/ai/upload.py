"""
文件上传/下载 API

支持用户上传文件到 Agent workspace，Agent 可通过 read_file 等工具访问。
"""

import asyncio
import shutil
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

from app.core.ctx import CTX_USER_ID
from app.schemas.base import Success, Fail

router = APIRouter(prefix="/upload", tags=["文件上传"])

# 无需鉴权的临时媒体接口（供 publish_conversation_media 等媒体发布和 QuickRouter 回调使用）
public_router = APIRouter(prefix="/upload", tags=["文件上传"])

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"
_USERS_ROOT = _WORKSPACE / "users"
_STATIC_ROOT = _PROJECT_ROOT / "static"
_DATA_ROOT = _PROJECT_ROOT / "data"

# 临时图片 token 缓存：{token: (file_path, expire_at)}
_IMG_TOKENS: dict[str, tuple[Path, datetime]] = {}


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """按 magic bytes 识别图片真实格式。文件名扩展名不可信（.png 名 JPEG 字节常见），
    服务端按扩展名推导 Content-Type，与实际字节不符会被多模态服务偶发拒收。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


def _prepare_media_upload(data: bytes, mime: str, ext: str) -> tuple[str, str]:
    """规范化待上传媒体的 (mime, ext)。

    扩展名优先用调用方传的真实后缀（保证服务端按正确 Content-Type 返回，
    视频 .mkv/.mov 等若从 mime 推导会得到错误扩展名）；缺省才从 mime 推导。
    图片以字节为准纠正 mime 与扩展名：调用方给的 mime 多由文件名推导、ext 也是文件名
    后缀，二者都可能与真实格式脱节（典型：.png 文件名实为 JPEG 字节；或上游
    _maybe_resize_sync 重编码后格式已变）。服务端按扩展名推导 Content-Type，与实际
    字节不一致会被多模态服务偶发拒收；视频不受影响，保持调用方 mime/扩展名。
    """
    if not ext:
        ext = (mime.split("/")[-1] if "/" in mime else "png") or "png"
        if ext == "jpeg":
            ext = "jpg"
    ext = ext.lstrip(".")
    if mime.startswith("image/"):
        sniffed = _sniff_image_mime(data)
        if sniffed:
            mime = sniffed
        _IMG_EXT_BY_MIME = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif", "bmp": "bmp"}
        ext = _IMG_EXT_BY_MIME.get(mime.split("/")[-1].lower(), ext)
    return mime, ext


def publish_conversation_media_sync(data: bytes, mime: str, ext: str = "") -> str:
    """同步版 publish_conversation_media（供同步工具/线程内调用，语义完全一致）。"""
    from app.langchain.config import langchain_config

    public_base = (langchain_config.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not public_base:
        logger.warning("[publish_conversation_media] 未配置 PUBLIC_BASE_URL，无法生成公网 URL")
        return ""

    import httpx

    mime, ext = _prepare_media_upload(data, mime, ext)
    token = ""
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{public_base}/api/v1/ai/upload/img-direct",
                    files={"file": (f"conv.{ext}", data, mime)},
                )
            resp.raise_for_status()
            token = resp.json()["token"]
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                logger.warning(f"[publish_conversation_media] 媒体上传公网服务失败（第 {attempt + 1} 次），1.5s 后重试: {type(e).__name__}: {e}")
                import time as _time

                _time.sleep(1.5)
    if not token:
        logger.warning(f"[publish_conversation_media] 媒体上传公网服务失败（重试耗尽）: {type(last_err).__name__}: {last_err}")
        return ""
    url = f"{public_base}/api/v1/ai/upload/img/{token}"
    logger.info(f"[publish_conversation_media] 媒体公网 URL: {url}（{len(data)} bytes, {mime}）")
    return url


async def publish_conversation_media(data: bytes, mime: str, ext: str = "") -> str:
    """把图片/视频发布为公网可访问的 URL（对话多模态直传用）。

    与 video_tools._upload_media_public 同一套机制：HTTP multipart 上传到
    {PUBLIC_BASE_URL}/api/v1/ai/upload/img-direct，由对外提供 PUBLIC_BASE_URL 的那台
    服务把文件落进它自己的 _tmp_imgs/（token 即文件名，磁盘持久、不过期），
    返回 {PUBLIC_BASE_URL}/api/v1/ai/upload/img/{token}（按 mime 返回正确 Content-Type，
    图片/视频通用）。不能改成"进程内注册 token + 本地文件"：agent 进程与对外服务常不是
    同一部署，本地文件对外不可见，URL 会 404。未配置 PUBLIC_BASE_URL 或上传失败返回空串。
    """
    from app.langchain.config import langchain_config

    public_base = (langchain_config.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not public_base:
        logger.warning("[publish_conversation_media] 未配置 PUBLIC_BASE_URL，无法生成公网 URL")
        return ""

    import httpx

    mime, ext = _prepare_media_upload(data, mime, ext)
    # 瞬时网络抖动（SSL/连接偶发重置）会把图片静默跳过，表现为"模型不认图"，
    # 发布失败先短重试再放弃；异常日志带上类型，避免出现空消息悬案
    token = ""
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{public_base}/api/v1/ai/upload/img-direct",
                    files={"file": (f"conv.{ext}", data, mime)},
                )
            resp.raise_for_status()
            token = resp.json()["token"]
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                logger.warning(f"[publish_conversation_media] 媒体上传公网服务失败（第 {attempt + 1} 次），1.5s 后重试: {type(e).__name__}: {e}")
                await asyncio.sleep(1.5)
    if not token:
        logger.warning(f"[publish_conversation_media] 媒体上传公网服务失败（重试耗尽）: {type(last_err).__name__}: {last_err}")
        return ""
    url = f"{public_base}/api/v1/ai/upload/img/{token}"
    logger.info(f"[publish_conversation_media] 媒体公网 URL: {url}（{len(data)} bytes, {mime}）")
    return url


# 兼容别名（图片直传最早引入时的名字）
publish_conversation_image = publish_conversation_media


def _user_upload_dir(user_id: int | str) -> Path:
    d = _USERS_ROOT / str(user_id) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique_filename(directory: Path, name: str) -> str:
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = name
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


@router.post("/file", summary="上传文件到 Agent workspace")
async def upload_file(
        file: UploadFile = File(...),
        session_key: str = Form(...),
):
    uid = CTX_USER_ID.get() or None
    if not uid:
        return Fail(msg="未登录，无法上传文件")

    upload_dir = _user_upload_dir(uid)
    filename = _unique_filename(upload_dir, file.filename or "unnamed")
    dest = upload_dir / filename

    try:
        def _save_to_disk() -> None:
            with open(dest, "wb") as f:
                shutil.copyfileobj(file.file, f, length=65536)
        await asyncio.to_thread(_save_to_disk)
    finally:
        await file.close()

    rel_path = f"uploads/{filename}"
    size = (await asyncio.to_thread(dest.stat)).st_size

    return Success(data={
        "filename": filename,
        "path": rel_path,
        "size": size,
    })


@router.post("/quick-action-image", summary="上传快捷功能预览图片")
async def upload_quick_action_image(
        file: UploadFile = File(...),
):
    """上传快捷功能预览图片到 data 目录，返回可公开访问的 URL 路径"""
    uid = CTX_USER_ID.get() or None
    if not uid:
        return Fail(msg="未登录，无法上传文件")

    # 验证文件类型
    if not file.content_type or not file.content_type.startswith('image/'):
        return Fail(msg="只能上传图片文件")

    # 验证文件大小（5MB）
    file_content = await file.read()
    if len(file_content) > 5 * 1024 * 1024:
        return Fail(msg="图片大小不能超过 5MB")

    # 存储到 data/quick-action-images/ 目录
    upload_dir = _DATA_ROOT / "quick-action-images"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 生成唯一文件名（时间戳 + 随机字符）
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_urlsafe(8)
    ext = Path(file.filename or "image").suffix or ".jpg"
    filename = f"{timestamp}_{random_suffix}{ext}"
    dest = upload_dir / filename

    try:
        def _save_to_disk() -> None:
            with open(dest, "wb") as f:
                f.write(file_content)
        await asyncio.to_thread(_save_to_disk)
    finally:
        await file.close()

    # 返回通过下载接口访问的路径
    rel_path = f"/api/v1/ai/upload/quick-action-image/{filename}"
    size = len(file_content)

    return Success(data={
        "filename": filename,
        "path": rel_path,
        "size": size,
    })


@public_router.get("/quick-action-image/{filename}", summary="获取快捷功能预览图片（公开访问）")
async def get_quick_action_image(filename: str):
    """获取快捷功能预览图片（无需认证）"""
    import mimetypes

    # 安全检查：防止路径穿越
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    file_path = _DATA_ROOT / "quick-action-images" / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path=str(file_path), media_type=media_type)


def register_tmp_image(file_path: Path, ttl_seconds: int = 300) -> str:
    """把本地文件路径注册为临时 token，供进程内调用（图片/视频/音频均可）。"""
    now = datetime.utcnow()
    expired = [t for t, (_, exp) in _IMG_TOKENS.items() if exp < now]
    for t in expired:
        p, _ = _IMG_TOKENS.pop(t)
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    token = secrets.token_urlsafe(24)
    _IMG_TOKENS[token] = (file_path, now + timedelta(seconds=ttl_seconds))
    return token


# 供 video_tools 等进程内调用，TTL 默认 1800s（覆盖 Ark 最长 12 分钟任务）
register_tmp_media = register_tmp_image


@public_router.post("/img-direct", summary="上传临时媒体文件（无需认证，供 publish_conversation_media 等发布机制使用）")
async def upload_tmp_image(file: UploadFile = File(...)):
    """接收图片二进制，存到 _tmp_imgs/，返回 token 供后续访问。"""
    tmp_dir = _WORKSPACE / "_tmp_imgs"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "img.bin").suffix or ".bin"
    # token 即文件名前缀，无需 _IMG_TOKENS，多 worker / 重启后仍可通过磁盘找到
    token = secrets.token_urlsafe(24)
    dest = tmp_dir / f"{token}{ext}"

    def _save():
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

    await asyncio.to_thread(_save)
    await file.close()
    return {"token": token}


@public_router.get("/img/{token}", summary="临时文件访问（图片/视频/音频，无需认证）")
async def get_tmp_image(token: str):
    """通过临时 token 直接返回文件，供外部服务拉取。"""
    import mimetypes

    # 优先走内存注册表（进程内 register_tmp_image 用法）
    entry = _IMG_TOKENS.get(token)
    if entry:
        file_path, expire_at = entry
        if datetime.utcnow() > expire_at:
            _IMG_TOKENS.pop(token, None)
        elif file_path.exists():
            media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            return FileResponse(path=str(file_path), media_type=media_type)

    # 兜底：按 token 前缀在 _tmp_imgs 目录查找文件（多 worker / 重启安全）。
    # 对话多模态直传图会永久留在消息历史/checkpoint 里，后续任意轮次与会话回放
    # 都要重新拉取，因此这里不设过期，只要文件在就返回。
    tmp_dir = _WORKSPACE / "_tmp_imgs"
    if tmp_dir.exists():
        matches = list(tmp_dir.glob(f"{token}.*"))
        if matches:
            file_path = matches[0]
            if file_path.exists():
                media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                return FileResponse(path=str(file_path), media_type=media_type)

    raise HTTPException(status_code=404, detail="文件不存在或已过期")


@public_router.delete("/img/{token}", summary="删除临时图片")
async def delete_tmp_image(token: str):
    """删除临时图片文件并注销 token。"""
    entry = _IMG_TOKENS.pop(token, None)
    if entry:
        file_path, _ = entry
        try:
            await asyncio.to_thread(file_path.unlink, True)
        except Exception:
            pass
    return {"ok": True}
