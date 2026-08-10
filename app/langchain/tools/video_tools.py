"""
视频生成工具。

由 get_video_tools() 按 VIDEO 能力（load_provider("VIDEO")）动态选择暴露：
  - ark / openrouter：火山方舟 Seedance 2.0（多模态参考）
  - apipod：APIPod Grok Imagine 1.5（纯图生视频 I2V）
  - 其它（happyhorse 等）：阿里云 HappyHorse（T2V/I2V/R2V/Video-Edit）

LLM 只会看到一个 generate_video 和 query_video_task；VIDEO 能力未配置时返回空列表。

文件组织：共用辅助 → 各 provider 辅助（Ark / HappyHorse / APIPod）→ 各 provider 工具 → get_video_tools()。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Optional, Union

import httpx
from langchain.tools import tool
from loguru import logger

from app.langchain.config import has_capability, load_provider
from app.models.standard.agent import AgentArtifact
from app.services import apipod_video_client as apipod
from app.services import ark_video_client as ark
from app.services import happyhorse_client as hh
from app.services.agent_runtime.call_context import get_agent_call_context
from app.services.dashscope_oss import (
    DashScopeOssError,
    is_remote_uri,
    upload_for_dashscope,
)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"

_HH_MAX_WAIT = 600.0
_HH_POLL = 15.0
_ARK_MAX_WAIT = 720.0
_ARK_POLL = 10.0
_APIPOD_MAX_WAIT = 720.0

_ARK_REF_TYPE_MAP = {
    "image": "image_url",
    "image_url": "image_url",
    "video": "video_url",
    "video_url": "video_url",
    "audio": "audio_url",
    "audio_url": "audio_url",
}


# ── 共用辅助函数 ─────────────────────────────────────────────────────────────

def _resolve_workspace() -> Path:
    ctx = get_agent_call_context()
    if ctx and ctx.workspace_dir:
        return Path(ctx.workspace_dir)
    return _DEFAULT_WORKSPACE


def _resolve_local_path(ref: str) -> Optional[Path]:
    if not ref:
        return None
    workspace = _resolve_workspace().resolve()
    p = Path(ref)
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p.resolve())
    else:
        rel = ref.lstrip("/").lstrip("\\")
        candidates.append((workspace / rel).resolve())
        candidates.append((_PROJECT_ROOT / rel).resolve())
    for c in candidates:
        try:
            if c.is_file():
                c.relative_to(workspace)
                return c
        except ValueError:
            continue
        except OSError:
            continue
    return None


def _coerce_media_list(v: Any, *, name: str = "media") -> Optional[list[dict[str, Any]]]:
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} 参数不是合法 JSON：{e}") from e
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError(f"{name} 解析后必须是数组，得到 {type(parsed).__name__}")
        return parsed
    raise ValueError(f"{name} 必须是数组或 JSON 字符串，得到 {type(v).__name__}")


async def _download_to_workspace(url: str, workspace: Path, filename: str) -> tuple[Path, int]:
    """下载产物视频到工作区。

    trust_env=False：产物 URL（s.apipod.ai / 火山 TOS / 阿里云 OSS 等）均可直连，
    而本机 Clash 等系统代理会掐断到部分产物 CDN 的 TLS；瞬时网络错误自动重试。
    """
    videos_dir = workspace / "videos"
    await asyncio.to_thread(videos_dir.mkdir, parents=True, exist_ok=True)
    target = videos_dir / filename
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True, trust_env=False) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(target, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            f.write(chunk)
            size = (await asyncio.to_thread(target.stat)).st_size
            return target, size
        except httpx.TransportError as e:
            last_exc = e
            if attempt < 3:
                logger.warning(f"产物视频下载瞬时网络错误（第 {attempt}/3 次），重试：{type(e).__name__}")
                await asyncio.sleep(1.0 * attempt)
    raise last_exc  # type: ignore[misc]


async def _register_video_artifact(
    *,
    name: str,
    rel_to_project: str,
    size: int,
    description: Optional[str],
) -> Optional[int]:
    ctx = get_agent_call_context()
    try:
        token = secrets.token_urlsafe(24)
        obj = await AgentArtifact.create(
            artifact_type="video",
            name=name[:200],
            description=(description[:1000] if description else None),
            path=rel_to_project,
            size=size,
            download_token=token,
            message_id=ctx.message_id if ctx else None,
            session_id=ctx.session_id if ctx else None,
        )
        return obj.id
    except Exception:
        logger.exception("登记视频 artifact 失败")
        return None


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# 本地文件 → 公网 URL 的缓存：sha256 -> (url, expire_at)。TTL 29 分钟（token 1800s 留余量）
_UPLOAD_CACHE: dict[str, tuple[str, float]] = {}
_UPLOAD_LOCK = asyncio.Lock()
_UPLOAD_TTL = 1740


async def _upload_media_public(path: Path) -> str:
    """把本地文件转成外部视频服务可公网拉取的 URL（图片/视频/音频通用）。

    走 HTTP multipart 上传到 `{PUBLIC_BASE_URL}/api/v1/ai/upload/img-direct`，
    由**对外提供 PUBLIC_BASE_URL 的那台服务**把文件落进它自己的 `_tmp_imgs/`，
    再经 `/api/v1/ai/upload/img/{token}` 供外部服务拉取（磁盘存储，多 worker / 重启安全，
    mtime TTL 1800s）。这与 publish_conversation_media 用的是同一套机制。

    不能改成"本进程内存注册 token"：agent 进程与对外服务常常不是同一个部署
    （本地开发指向线上域名、或多副本），内存 token 与本地文件对外都不可见，URL 会 404。
    按文件 sha256 缓存 29 分钟（短于 1800s 磁盘 TTL，避免缓存到已过期 URL）。
    """
    from app.langchain.config import langchain_config
    public_base = (langchain_config.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not public_base:
        raise ValueError("未配置 PUBLIC_BASE_URL，无法为本地素材生成公网 URL，请在 .env 中设置")

    sha = await asyncio.to_thread(_file_sha256, path)
    now = time.time()
    async with _UPLOAD_LOCK:
        hit = _UPLOAD_CACHE.get(sha)
        if hit and hit[1] > now:
            logger.debug(f"media 公网 URL 缓存命中：{path.name}")
            return hit[0]

    file_bytes = await asyncio.to_thread(path.read_bytes)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{public_base}/api/v1/ai/upload/img-direct",
                files={"file": (path.name, file_bytes, mime)},
            )
        resp.raise_for_status()
        token = resp.json()["token"]
    except Exception as e:
        raise ValueError(
            f"素材上传到公网服务失败：{e}（请确认服务能访问 PUBLIC_BASE_URL={public_base}）"
        ) from e
    url = f"{public_base}/api/v1/ai/upload/img/{token}"
    logger.info(f"本地素材已上传公网 URL：{path.name} -> {url}")

    async with _UPLOAD_LOCK:
        _UPLOAD_CACHE[sha] = (url, now + _UPLOAD_TTL)
    return url


async def _data_uri_to_tmp_file(data_uri: str) -> Path:
    """把 data:{mime};base64,{b64} 解码落盘到工作区 tmp/，返回临时文件路径（扩展名按 mime 推断）。"""
    try:
        head, b64_part = data_uri.split(",", 1)
        raw = base64.b64decode(b64_part, validate=False)
    except Exception as e:
        raise ValueError(f"无法解析 data: 格式的素材：{e}") from e
    mime = head[5:].split(";", 1)[0].strip() if head.startswith("data:") else ""
    ext = mimetypes.guess_extension(mime or "image/png") or ".png"
    if ext == ".jpe":  # guess_extension 对 image/jpeg 会返回 .jpe，统一成 .jpg
        ext = ".jpg"
    tmp_dir = _resolve_workspace() / "tmp"
    await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"media-ref-{secrets.token_hex(6)}{ext}"
    await asyncio.to_thread(tmp_path.write_bytes, raw)
    return tmp_path


async def _image_to_public_url(ref: str) -> str:
    """把 agent 传入的图片引用统一转成外部服务可公网拉取的 https URL（Ark / APIPod 共用）。

    外部视频服务的 image_url 需要真实公网 URL（服务端要自己去拉），因此：
      - http(s) 公网链接       → 原样直通
      - data:image/...;base64  → 解码落盘后经 _upload_media_public 换公网 URL
      - 工作区相对路径          → 如 'uploads/x.png'、'images/x.png'，读出后换公网 URL
      - 工作区内绝对路径        → 同上
    """
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("图片引用不能为空")

    if ref.startswith(("http://", "https://")):
        logger.info(f"[image→公网] 已是公网 URL，直通：{ref[:120]}")
        return ref

    if ref.startswith("data:"):
        tmp = await _data_uri_to_tmp_file(ref)
        url = await _upload_media_public(tmp)
        logger.info(f"[image→公网] data: 已换公网 URL：{url}")
        return url

    local = _resolve_local_path(ref)
    if local is None:
        raise ValueError(
            f"找不到图片文件：{ref}（支持 http(s) 公网链接、data: base64，"
            f"或工作区相对路径如 'uploads/x.png'、'images/x.png'）"
        )
    url = await _upload_media_public(local)
    logger.info(f"[image→公网] 本地 {local.name} 已换公网 URL：{url}")
    return url


async def _finalize_video(
    *,
    result: dict[str, Any],
    format_result: Callable[..., str],
    video_url: Optional[str],
    name: Optional[str],
    fallback_name: str,
    description: Optional[str],
) -> str:
    """三个 provider 共用的收尾：下载视频到工作区 + 登记 artifact + 格式化返回。"""
    if not video_url:
        return format_result(result) + "\n\n⚠️ 状态成功但未返回 video_url，无法保存"

    workspace = _resolve_workspace()
    fname = (name or fallback_name).strip()
    if not fname.lower().endswith(".mp4"):
        fname = f"{fname}.mp4"
    fname = fname.replace("/", "_").replace("\\", "_")[:120]

    try:
        abs_path, size = await _download_to_workspace(video_url, workspace, fname)
        rel_to_project = abs_path.resolve().relative_to(_PROJECT_ROOT.resolve())
        rel_str = str(rel_to_project).replace("\\", "/")
        artifact_id = await _register_video_artifact(
            name=fname,
            rel_to_project=rel_str,
            size=size,
            description=description,
        )
    except Exception as e:
        logger.exception(f"视频下载/登记失败（{fallback_name}）")
        return format_result(result) + (
            f"\n\n⚠️ 下载或登记失败：{e}\n（视频 URL 24h 内有效，可手动下载）"
        )

    return format_result(result, artifact_id=artifact_id, local_path=rel_str)


# ── Ark 专用辅助 ─────────────────────────────────────────────────────────────

def _ark_is_remote_url(s: Optional[str]) -> bool:
    if not s or not isinstance(s, str):
        return False
    return s.strip().startswith(("http://", "https://", "data:"))


async def _ark_build_content(
    prompt: str,
    references: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for idx, item in enumerate(references or []):
        if not isinstance(item, dict):
            raise ValueError(f"media[{idx}] 必须是对象")
        raw_type = (item.get("type") or "").strip().lower()
        ark_type = _ARK_REF_TYPE_MAP.get(raw_type)
        if ark_type is None:
            raise ValueError(f"media[{idx}].type 必须是 image / video / audio")
        url = (item.get("url") or "").strip()
        if not url:
            raise ValueError(f"media[{idx}].url 不能为空")
        if ark_type == "image_url":
            # 与 APIPod 一致：图片统一换成公网 URL（http(s) 直通，data:/本地自动上传）
            url = await _image_to_public_url(url)
        elif not _ark_is_remote_url(url):
            local = _resolve_local_path(url)
            if local is None:
                raise ValueError(f"media[{idx}].url 找不到工作区文件：{url}")
            url = await _upload_media_public(local)
        node: dict[str, Any] = {"type": ark_type, ark_type: {"url": url}}
        if role := item.get("role"):
            node["role"] = str(role).strip()
        content.append(node)
    return content


def _ark_format_result(
    result: dict[str, Any],
    *,
    artifact_id: Optional[int] = None,
    local_path: Optional[str] = None,
) -> str:
    status = result.get("status")
    lines = [f"任务状态：{status}"]
    if rt_id := result.get("task_id"):
        lines.append(f"task_id：{rt_id}")
    if status == "succeeded":
        usage = result.get("usage") or {}
        if dur := (result.get("duration") or usage.get("duration")):
            lines.append(f"时长：{dur}s")
        if reso := (result.get("resolution") or usage.get("resolution")):
            lines.append(f"分辨率：{reso}")
        if ratio := result.get("ratio"):
            lines.append(f"比例：{ratio}")
        if artifact_id:
            lines.append(
                f"已登记产物 artifact#{artifact_id}（对话里前端会自动展示视频卡片，无需在文本里贴 URL；"
                f"挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{artifact_id}/download?inline=1）"
            )
        if local_path:
            lines.append(f"本地保存：{local_path}")
        if result.get("video_url"):
            lines.append(f"原始 URL（24h 内有效）：{result['video_url']}")
    elif status in ("failed", "cancelled", "canceled", "timeout", "unknown"):
        if code := result.get("error_code"):
            lines.append(f"错误码：{code}")
        if msg := result.get("error_message"):
            lines.append(f"错误信息：{msg}")
    else:
        lines.append("（仍在处理中）")
    return "\n".join(lines)


# ── HappyHorse 专用辅助 ───────────────────────────────────────────────────────

async def _hh_normalize_media_url(ref: str, *, model: str) -> str:
    ref = ref.strip()
    if is_remote_uri(ref):
        return ref
    local = _resolve_local_path(ref)
    if local is None:
        raise DashScopeOssError(f"找不到本地文件：{ref}")
    return await upload_for_dashscope(local, model=model)


async def _hh_normalize_media_list(
    media: list[dict[str, Any]], *, model: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(media):
        if not isinstance(item, dict):
            raise DashScopeOssError(f"media[{idx}] 必须是对象")
        new_item = dict(item)
        url = new_item.get("url")
        if isinstance(url, str) and url.strip():
            new_item["url"] = await _hh_normalize_media_url(url, model=model)
        out.append(new_item)
    return out


def _hh_format_result(
    result: dict[str, Any],
    *,
    artifact_id: Optional[int] = None,
    local_path: Optional[str] = None,
) -> str:
    status = result.get("status")
    lines = [f"任务状态：{status}"]
    if rt_id := result.get("task_id"):
        lines.append(f"task_id：{rt_id}")
    if status == "SUCCEEDED":
        usage = result.get("usage") or {}
        sr = usage.get("SR")
        ratio = usage.get("ratio")
        out_dur = usage.get("output_video_duration") or usage.get("duration")
        in_dur = usage.get("input_video_duration") or 0
        cost = hh.estimate_cost(
            f"{sr}P" if sr else None,
            int(out_dur) + int(in_dur) if out_dur is not None else None,
        )
        lines.append(f"分辨率：{sr}P · 宽高比：{ratio} · 时长：{out_dur}s")
        if cost is not None:
            lines.append(f"预估费用：¥{cost}")
        if artifact_id:
            lines.append(
                f"已登记产物 artifact#{artifact_id}（对话里前端会自动展示视频卡片，无需在文本里贴 URL；"
                f"挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{artifact_id}/download?inline=1）"
            )
        if local_path:
            lines.append(f"本地保存：{local_path}")
        if result.get("video_url"):
            lines.append(f"原始 URL（24h 内有效）：{result['video_url']}")
    elif status in ("FAILED", "UNKNOWN", "CANCELED", "TIMEOUT"):
        if code := result.get("error_code"):
            lines.append(f"错误码：{code}")
        if msg := result.get("error_message"):
            lines.append(f"错误信息：{msg}")
    else:
        lines.append("（仍在处理中）")
    return "\n".join(lines)


# ── APIPod 专用辅助 ───────────────────────────────────────────────────────────

def _apipod_format_result(
    result: dict[str, Any],
    *,
    artifact_id: Optional[int] = None,
    local_path: Optional[str] = None,
) -> str:
    status = result.get("status")
    lines = [f"任务状态：{status}"]
    if rt_id := result.get("task_id"):
        lines.append(f"task_id：{rt_id}")
    if status == "completed":
        lines.append("分辨率：480p")
        if artifact_id:
            lines.append(
                f"已登记产物 artifact#{artifact_id}（对话里前端会自动展示视频卡片，无需在文本里贴 URL；"
                f"挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{artifact_id}/download?inline=1）"
            )
        if local_path:
            lines.append(f"本地保存：{local_path}")
        if result.get("video_url"):
            lines.append(f"原始 URL（24h 内有效）：{result['video_url']}")
    elif status in ("failed", "cancelled", "canceled", "timeout", "unknown"):
        if msg := result.get("error_message"):
            lines.append(f"错误信息：{msg}")
    else:
        lines.append("（仍在处理中）")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# HappyHorse Tools
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def generate_happyhorse_video(
    model: Annotated[
        str,
        "happyhorse-1.0-t2v（纯文字）/ -i2v（图+文）/ -r2v（多张参考图+文）/ -video-edit（视频+文）",
    ],
    prompt: Annotated[
        Optional[str],
        "文本提示词。详细描述（80~120 字）效果远优于简短描述。"
        "T2V/R2V/Video-Edit 必填；I2V 可选但强烈建议。"
        "R2V 中需要用 character1/character2 等指代 media 数组的图片。",
    ] = None,
    media: Annotated[
        Optional[Union[list[dict], str]],
        "媒体输入，T2V 不需要。"
        "I2V：[{type:'first_frame'|'last_frame', url:'...'}]，1~2 项；"
        "R2V：[{type:'reference_image', url:'...'}, ...]，1~9 项；"
        "Video-Edit：1 个 {type:'video', url:'...'} + 可选若干 reference_image。"
        "url 支持 http(s)、oss://、data:base64、工作区相对路径（自动上传）。优先传数组，JSON 字符串也可解析。",
    ] = None,
    resolution: Annotated[
        str, "720P 或 1080P（该模型最低档即 720P，无 480P）。默认 720P（¥0.9/s），定稿再升 1080P（¥1.6/s）"
    ] = "720P",
    duration: Annotated[
        int, "视频时长 3~15 秒（整数）。Video-Edit 不支持，会被忽略"
    ] = 5,
    ratio: Annotated[
        Optional[str],
        "宽高比 16:9 / 9:16 / 1:1 / 4:3 / 3:4。I2V 和 Video-Edit 不支持，传了会被忽略",
    ] = "16:9",
    seed: Annotated[Optional[int], "随机种子，固定可复现（0~2147483647）"] = None,
    watermark: Annotated[bool, "是否加右下角水印"] = True,
    prompt_extend: Annotated[
        Optional[bool], "是否让平台智能改写/扩展 prompt（短 prompt 时建议开启）"
    ] = None,
    name: Annotated[
        Optional[str], "产物展示名（含 .mp4 后缀），不传按 prompt 截断生成"
    ] = None,
) -> str:
    """
    用阿里云 HappyHorse 生成视频，内部轮询最长等待 10 分钟。
    成功后自动登记 artifact、前端展示视频卡片——不要在文本里贴 URL，简短说明视频内容即可。

    模式选择（优先级从高到低）：用户给参考图（1~9 张）→ **优先 r2v**（比 i2v 更灵活可控，
    prompt 中用 character1/character2 指代对应图）；明确要求"以这张图为首帧/尾帧精确延续" → i2v
    （不支持 ratio/duration）；只有文字 → t2v；给了视频 → video-edit。
    prompt 越具体越好：镜头语言、画面元素、光影氛围、动作节奏都写清楚。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频生成不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"

    try:
        media_list = _coerce_media_list(media)
    except ValueError as e:
        return f"❌ {e}"

    parameters: dict[str, Any] = {
        "resolution": resolution,
        "watermark": watermark,
    }
    if model in (hh.MODEL_T2V, hh.MODEL_R2V):
        parameters["ratio"] = ratio or "16:9"
    if model != hh.MODEL_VIDEO_EDIT:
        parameters["duration"] = int(duration)
    if seed is not None:
        parameters["seed"] = int(seed)
    if prompt_extend is not None:
        parameters["prompt_extend"] = bool(prompt_extend)

    if media_list:
        try:
            media_list = await _hh_normalize_media_list(media_list, model=model)
        except DashScopeOssError as e:
            return f"❌ 媒体素材处理失败：{e}"
        except Exception as e:
            logger.exception("media 归一化异常")
            return f"❌ 媒体素材处理失败：{e}"

    try:
        submit = await hh.submit_task(
            model=model,
            prompt=prompt,
            media=media_list,
            parameters=parameters,
        )
    except hh.HappyHorseError as e:
        return f"❌ 提交失败：{e}"
    except Exception as e:
        logger.exception("generate_happyhorse_video 提交异常")
        return f"❌ 提交失败：{e}"

    task_id = submit.task_id
    logger.info(f"HappyHorse 任务已提交 {task_id}（model={model}）")

    try:
        result = await hh.wait_task(
            task_id,
            poll_interval=_HH_POLL,
            max_wait=_HH_MAX_WAIT,
        )
    except Exception as e:
        logger.exception(f"HappyHorse 任务 {task_id} 轮询异常")
        return f"❌ 轮询失败（task_id={task_id}）：{e}"

    if result.get("status") != "SUCCEEDED":
        return _hh_format_result(result)

    try:
        from app.langchain.billing.pricing import Billing
        usage = result.get("usage") or {}
        sr = usage.get("SR")
        out_dur = int(usage.get("output_video_duration") or usage.get("duration") or 0)
        in_dur = int(usage.get("input_video_duration") or 0)
        total_sec = out_dur + in_dur
        unit_key = f"video_sec_{sr}" if sr in (720, 1080) else "video_sec_720"
        if total_sec > 0:
            await Billing.record(
                module="video",
                provider="dashscope",
                model=model,
                units={unit_key: total_sec},
                ref_type="video_task",
            )
    except Exception:
        logger.exception("[Billing] 视频计费失败（已忽略）")

    usage = result.get("usage") or {}
    desc_parts: list[str] = []
    if prompt:
        desc_parts.append(prompt[:160])
    if usage.get("SR"):
        desc_parts.append(f"{usage['SR']}P")
    if usage.get("output_video_duration"):
        desc_parts.append(f"{usage['output_video_duration']}s")

    return await _finalize_video(
        result=result,
        format_result=_hh_format_result,
        video_url=result.get("video_url"),
        name=name,
        fallback_name=f"video-{task_id[:8]}",
        description=" · ".join(desc_parts) if desc_parts else None,
    )


@tool
async def query_happyhorse_video_task(
    task_id: Annotated[str, "DashScope 返回的 task_id（24 小时有效）"],
) -> str:
    """
    查询已存在视频任务的进度或结果。仅在用户明确给出旧 task_id 时使用；
    新生成视频请直接用 generate_happyhorse_video（内部已自动轮询）。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频查询不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"
    try:
        result = await hh.query_task(task_id)
    except hh.HappyHorseError as e:
        return f"❌ 查询失败：{e}"
    except Exception as e:
        logger.exception("query_happyhorse_video_task 异常")
        return f"❌ 查询失败：{e}"
    return _hh_format_result(result)


# ══════════════════════════════════════════════════════════════════════════════
# Ark Tools
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def generate_ark_video(
    prompt: Annotated[
        str,
        "文本提示词，详细描述（80~200 字）效果远优于简短描述；可用镜头语言、节拍标注、镜头切换时间增强表现力",
    ],
    references: Annotated[
        Optional[Union[list[dict], str]],
        "参考素材数组，每项 {type, url, role?}。type ∈ image/video/audio；"
        "url 支持 http(s)、data:base64、工作区相对路径（自动处理）。"
        "role：reference_image / reference_video / reference_audio / first_frame（首帧）/ last_frame（尾帧）。"
        "上限：图 9 张、视频 3 段、音频 3 段，且不能只给音频",
    ] = None,
    ratio: Annotated[
        Optional[str], "画面宽高比 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9"
    ] = "16:9",
    duration: Annotated[
        Optional[int],
        "视频时长（秒）：-1 = 自适应（按参考素材决定）；或显式指定 4~15",
    ] = -1,
    fps: Annotated[Optional[int], "帧率 24 / 30"] = None,
    generate_audio: Annotated[
        Optional[bool], "是否生成原生音频"
    ] = None,
    watermark: Annotated[Optional[bool], "是否打水印"] = False,
    camera_fixed: Annotated[Optional[bool], "是否固定机位"] = None,
    seed: Annotated[Optional[int], "随机种子，固定可复现"] = None,
    name: Annotated[
        Optional[str], "产物展示名（含 .mp4 后缀）"
    ] = None,
) -> str:
    """
    用火山方舟 Seedance 2.0 生成视频（纯文字 / 首尾帧 / 多模态参考均可），内部轮询最长等待 12 分钟。
    成功后自动登记 artifact、前端展示视频卡片——不要在文本里贴 URL，简短说明视频内容即可。
    纯文字生成不传 references；首帧生成传 references=[{type:'image', url, role:'first_frame'}]；固定 480p 省钱。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频生成不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"

    cfg = load_provider("VIDEO")
    if cfg.model:
        selected_model = cfg.model
    elif cfg.provider == "openrouter":
        selected_model = ark.MODEL_OR_SEEDANCE_2_0_FAST
    else:
        selected_model = ark.DEFAULT_MODEL

    try:
        ref_list = _coerce_media_list(references, name="references")
    except ValueError as e:
        return f"❌ {e}"

    try:
        content = await _ark_build_content(prompt, ref_list)
    except ValueError as e:
        return f"❌ {e}"

    try:
        submit = await ark.submit_task(
            model=selected_model,
            content=content,
            ratio=ratio,
            duration=duration,
            resolution="480p",  # 硬编码 480p：生视频很贵，统一最低档
            fps=fps,
            generate_audio=generate_audio,
            watermark=watermark,
            camera_fixed=camera_fixed,
            seed=seed,
        )
    except ark.ArkVideoError as e:
        return f"❌ 提交失败：{e}"
    except Exception as e:
        logger.exception("generate_ark_video 提交异常")
        return f"❌ 提交失败：{e}"

    task_id = submit.task_id
    logger.info(f"Ark 视频任务已提交 {task_id}（model={selected_model}）")

    try:
        result = await ark.wait_task(
            task_id,
            poll_interval=_ARK_POLL,
            max_wait=_ARK_MAX_WAIT,
        )
    except Exception as e:
        logger.exception(f"Ark 视频任务 {task_id} 轮询异常")
        return f"❌ 轮询失败（task_id={task_id}）：{e}"

    if result.get("status") != "succeeded":
        return _ark_format_result(result)

    try:
        from app.langchain.billing.pricing import Billing
        duration_sec = int(result.get("duration") or 0)
        resolution_str = str(result.get("resolution") or "480p")
        try:
            resolution_num = int(resolution_str.rstrip("pP"))
        except ValueError:
            resolution_num = 480
        if resolution_num not in (480, 720, 1080):
            resolution_num = 480
        unit_key = f"video_sec_{resolution_num}"
        if duration_sec > 0:
            await Billing.record(
                module="video",
                provider="ark",
                model=selected_model,
                units={unit_key: duration_sec},
                ref_type="ark_video_task",
            )
    except Exception:
        logger.exception("[Billing] Ark 视频计费失败（已忽略）")

    usage = result.get("usage") or {}
    desc_parts = [prompt[:160]] if prompt else []
    if reso := (result.get("resolution") or usage.get("resolution") or usage.get("SR")):
        desc_parts.append(str(reso))
    if dur := (result.get("duration") or usage.get("duration") or usage.get("video_duration")):
        desc_parts.append(f"{dur}s")

    return await _finalize_video(
        result=result,
        format_result=_ark_format_result,
        video_url=result.get("video_url"),
        name=name,
        fallback_name=f"ark-{task_id[:12]}",
        description=" · ".join(desc_parts) if desc_parts else None,
    )


@tool
async def query_ark_video_task(
    task_id: Annotated[str, "Ark 返回的任务 id（cgt-xxx，仅保留 7 天）"],
) -> str:
    """
    查询已存在 Ark 视频任务的进度或结果。仅在用户明确给出旧 task_id 时使用；
    新生成视频请直接用 generate_ark_video（内部已自动轮询）。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频查询不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"
    try:
        result = await ark.query_task(task_id)
    except ark.ArkVideoError as e:
        return f"❌ 查询失败：{e}"
    except Exception as e:
        logger.exception("query_ark_video_task 异常")
        return f"❌ 查询失败：{e}"
    return _ark_format_result(result)


# ══════════════════════════════════════════════════════════════════════════════
# APIPod (Grok Imagine) Tools
# ══════════════════════════════════════════════════════════════════════════════

@tool
async def generate_apipod_video(
    image_url: Annotated[
        str,
        "首帧参考图（必填——本模型是图生视频 I2V，prompt 只负责描述运动）。"
        "支持 http(s)、工作区相对路径、data:base64——本地会自动上传换成公网 URL",
    ],
    prompt: Annotated[
        str,
        "运动描述（必填）：描述画面怎么动起来——镜头运动（推拉摇移、环绕）、"
        "人物动作、场景氛围、物理细节。不要复述图片内容，只写'会发生什么变化'",
    ],
    aspect_ratio: Annotated[
        str, "宽高比 1:1 / 2:3 / 3:2 / 9:16 / 16:9。尽量与参考图比例一致，否则画面会被裁剪"
    ] = "16:9",
    duration: Annotated[int, "视频时长 1~15 秒（整数）"] = 10,
    name: Annotated[
        Optional[str], "产物展示名（含 .mp4 后缀）"
    ] = None,
) -> str:
    """
    用 APIPod Grok Imagine 1.5 生成视频（仅图生视频 I2V），内部轮询最长等待 12 分钟。
    成功后自动登记 artifact、前端展示视频卡片——不要在文本里贴 URL，简短说明视频内容即可。
    纯文字生视频需求：先用生图工具出一张首帧图再来动起来；或向用户说明当前全局视频模型为
    Grok（I2V），纯文生视频需超管切换到 Seedance / HappyHorse。分辨率固定 480p 省钱。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频生成不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"

    if aspect_ratio not in apipod.SUPPORTED_ASPECT_RATIOS:
        return f"❌ aspect_ratio 必须是 {' / '.join(apipod.SUPPORTED_ASPECT_RATIOS)} 之一"
    dur = max(1, min(15, int(duration)))

    cfg = load_provider("VIDEO")
    selected_model = cfg.model or apipod.DEFAULT_MODEL

    try:
        normalized_image = await _image_to_public_url(image_url)
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.exception("APIPod 参考图处理异常")
        return f"❌ 参考图处理失败：{e}"

    try:
        submit = await apipod.submit_task(
            model=selected_model,
            prompt=prompt,
            image_url=normalized_image,
            aspect_ratio=aspect_ratio,
            duration=dur,
        )
    except apipod.ApipodVideoError as e:
        return f"❌ 提交失败：{e}"
    except Exception as e:
        logger.exception("generate_apipod_video 提交异常")
        return f"❌ 提交失败：{e}"

    task_id = submit.task_id
    logger.info(f"APIPod 视频任务已提交 {task_id}（model={selected_model}，{dur}s，{aspect_ratio}）")

    try:
        result = await apipod.wait_task(task_id, max_wait=_APIPOD_MAX_WAIT)
    except Exception as e:
        logger.exception(f"APIPod 任务 {task_id} 轮询异常")
        return f"❌ 轮询失败（task_id={task_id}）：{e}"

    if result.get("status") != "completed":
        return _apipod_format_result(result)

    try:
        from app.langchain.billing.pricing import Billing
        await Billing.record(
            module="video",
            provider="apipod",
            model=selected_model,
            units={"video_sec_480": dur},
            ref_type="apipod_video_task",
        )
    except Exception:
        logger.exception("[Billing] APIPod 视频计费失败（已忽略）")

    desc_parts = [prompt[:160]] if prompt else []
    desc_parts.append(f"480p · {dur}s · {aspect_ratio}")

    return await _finalize_video(
        result=result,
        format_result=_apipod_format_result,
        video_url=result.get("video_url"),
        name=name,
        fallback_name=f"grok-{task_id[:8]}",
        description=" · ".join(desc_parts),
    )


@tool
async def query_apipod_video_task(
    task_id: Annotated[str, "APIPod 返回的 task_id"],
) -> str:
    """
    查询已存在 APIPod 视频任务的进度或结果。仅在用户明确给出旧 task_id 时使用；
    新生成视频请直接用 generate_apipod_video（内部已自动轮询）。
    """
    if not has_capability("VIDEO"):
        return "❌ 视频查询不可用：VIDEO 能力未配置（缺 VIDEO_PROVIDER / VIDEO_API_KEY）"
    try:
        result = await apipod.query_task(task_id)
    except apipod.ApipodVideoError as e:
        return f"❌ 查询失败：{e}"
    except Exception as e:
        logger.exception("query_apipod_video_task 异常")
        return f"❌ 查询失败：{e}"
    return _apipod_format_result(result)


# ── 能力开关：按 VIDEO_PROVIDER 返回当前启用的视频工具 ──────────────────────


def get_video_tools() -> list[Any]:
    """返回当前启用的视频生成/查询工具；VIDEO 能力未配置返回空列表。

    VIDEO_PROVIDER=ark/openrouter → 火山方舟 Seedance；apipod → Grok Imagine（I2V）；
    其它值（happyhorse 等）→ 阿里 HappyHorse。
    """
    if not has_capability("VIDEO"):
        return []
    provider = load_provider("VIDEO").provider
    if provider in ("ark", "openrouter"):
        return [generate_ark_video, query_ark_video_task]
    if provider == "apipod":
        return [generate_apipod_video, query_apipod_video_task]
    return [generate_happyhorse_video, query_happyhorse_video_task]
