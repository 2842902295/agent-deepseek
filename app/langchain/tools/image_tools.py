"""
图像生成 / 编辑工具。

由 get_image_tools() 按 IMAGE 能力（load_provider("IMAGE")）动态选择暴露：
  - gpt：QuickRouter GPT-image-2（同步），支持文生图和图像编辑
  - apipod：APIPod GPT-image-2（异步任务式），支持文生图和参考图编辑/多图融合
  - 其它（qwen 等）：阿里云千问 Qwen-Image，支持文生图和图像编辑/多图融合

LLM 只会看到一个图像生成工具；IMAGE 能力未配置时返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from pathlib import Path
from typing import Annotated, Any, Optional, Union

import httpx
from langchain.tools import tool
from loguru import logger

from app.langchain.config import has_capability, load_provider
from app.models.standard.agent import AgentArtifact
from app.services import apipod_image_client as api
from app.services import gpt_image_client as gi
from app.services import qwen_image_client as qi
from app.services.agent_runtime.call_context import get_agent_call_context
from app.services.dashscope_oss import (
    DashScopeOssError,
    is_remote_uri,
    upload_for_dashscope,
)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"


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


async def _normalize_image_ref(ref: str, *, model: str) -> str:
    ref = ref.strip()
    if is_remote_uri(ref):
        return ref
    local = _resolve_local_path(ref)
    if local is None:
        raise DashScopeOssError(f"找不到本地文件：{ref}（既不是 http(s)/oss/data: URL，也不是工作区内的文件）")
    return await upload_for_dashscope(local, model=model)


def _coerce_str_list(v: Any, *, name: str) -> Optional[list[str]]:
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return [s]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, dict):
            for k in ("image", "url"):
                if k in parsed:
                    return [str(parsed[k])]
        raise ValueError(f"{name} 解析后必须是字符串数组")
    raise ValueError(f"{name} 必须是字符串数组或 JSON 字符串")


_ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


def _filename_from_url(url: str, fallback_stem: str) -> str:
    base = url.split("?", 1)[0].rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    if "." in base:
        ext = base.rsplit(".", 1)[1].lower()
        if ext in _ALLOWED_EXT:
            return base
    return f"{fallback_stem}.png"


async def _download_image(url: str, target: Path) -> int:
    """下载到 target，返回字节数。支持 http(s) URL 和 data URI。

    trust_env=False：产物 URL（s.apipod.ai / DashScope OSS 等）均可直连，
    而本机 Clash 等系统代理会掐断到部分产物 CDN 的 TLS；瞬时网络错误自动重试。
    """
    # 处理 data URI（如 data:image/png;base64,...）
    if url.startswith("data:"):
        import base64

        if "," in url:
            header, data = url.split(",", 1)
            binary = base64.b64decode(data)
            with open(target, "wb") as f:
                f.write(binary)
            return len(binary)
        raise ValueError(f"data URI 格式错误：{url[:100]}")

    # 处理 http(s) URL
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, trust_env=False) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(target, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            f.write(chunk)
            return (await asyncio.to_thread(target.stat)).st_size
        except httpx.TransportError as e:
            last_exc = e
            if attempt < 3:
                logger.warning(f"产物下载瞬时网络错误（第 {attempt}/3 次），重试：{type(e).__name__}")
                await asyncio.sleep(1.0 * attempt)
    raise last_exc  # type: ignore[misc]


async def _register_image_artifact(
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
            artifact_type="image",
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
        logger.exception("登记图像 artifact 失败")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Qwen-Image Tool
# ══════════════════════════════════════════════════════════════════════════════


@tool
async def generate_qwen_image(
    prompt: Annotated[
        str,
        "正向提示词。文生图建议 80~200 字，详细描述主体/风格/构图/光照；图像编辑时是编辑指令。prompt 长度有上限（qwen-image-2.0 系列约 1300 token），超长会被平台拒绝。",
    ],
    images: Annotated[
        Optional[Union[list[str], str]],
        "输入图像（可选，1~3 张），用于图像编辑/多图融合。支持 http(s)、oss://、data:base64、工作区相对路径（自动上传）。不传则纯文生图。",
    ] = None,
    n: Annotated[
        int,
        "生成张数，1~6。部分模型固定 1 张（传多了会自动收为 1）。",
    ] = 1,
    size: Annotated[
        Optional[str],
        "输出分辨率，格式 宽*高（中间用星号）。"
        "推荐：2688*1536(16:9) / 1536*2688(9:16) / 2048*2048(1:1) / 2368*1728(4:3) / 1728*2368(3:4)；不传用平台默认。"
        "部分模型只接受固定几档尺寸，报尺寸无效时按推荐值重试。",
    ] = None,
    negative_prompt: Annotated[Optional[str], "反向提示词（不希望出现的内容），≤500 字符"] = None,
    prompt_extend: Annotated[
        Optional[bool],
        "是否让平台智能改写 prompt。默认 None（按平台默认 true）；prompt 已经很详细时显式传 false 控制更严。",
    ] = None,
    watermark: Annotated[bool, "是否右下角加 Qwen-Image 水印"] = False,
    seed: Annotated[Optional[int], "随机种子 0~2147483647，固定可复现"] = None,
    name: Annotated[
        Optional[str],
        "产物展示名前缀（不含扩展名）。多张时会自动加 -1/-2 后缀；不传按 prompt 截断生成。",
    ] = None,
    save_dir: Annotated[
        Optional[str],
        "图像保存的子目录（相对工作区的路径，如 'frames/ep01'、'output/poster'）。不传默认保存到 'images/'。",
    ] = None,
) -> str:
    """
    用阿里云千问 Qwen-Image 生成或编辑图像（同步阻塞，一次返回）。具体用哪个模型由平台模型配置决定，无需关心。
    文生图：只传 prompt（文字渲染尤其强）；图像编辑/多图融合：传 1~3 张 images。
    成功后自动登记 image artifact、前端展示图片卡片——不要在文本里贴 URL，简短交代生成内容即可。
    如需把生成的图再喂给其它工具，用工作区相对路径（如 images/xxx.png）。
    """
    if not has_capability("IMAGE"):
        return "❌ 图像生成不可用：IMAGE 能力未配置（缺 IMAGE_PROVIDER / IMAGE_API_KEY）"

    # 模型一律由平台配置的预设块决定（超管切换的真相源），工具不接受模型覆盖
    model = load_provider("IMAGE").model or qi.DEFAULT_MODEL

    try:
        images_list = _coerce_str_list(images, name="images")
    except ValueError as e:
        return f"❌ {e}"

    if images_list:
        normalized: list[str] = []
        for ref in images_list:
            try:
                normalized.append(await _normalize_image_ref(ref, model=model))
            except DashScopeOssError as e:
                return f"❌ 输入图像处理失败：{e}"
            except Exception as e:
                logger.exception("image 归一化异常")
                return f"❌ 输入图像处理失败：{e}"
        images_list = normalized

    try:
        result = await qi.generate(
            prompt=prompt,
            images=images_list,
            model=model,
            n=n,
            size=size,
            negative_prompt=negative_prompt,
            prompt_extend=prompt_extend,
            watermark=watermark,
            seed=seed,
        )
    except qi.QwenImageError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.exception("generate_qwen_image 调用异常")
        return f"❌ 调用失败：{e}"

    # ── 计费记账 ──────────────────────────────────────────────────────────────
    try:
        from app.langchain.billing.pricing import Billing

        image_count = len(result.images)
        if image_count > 0:
            await Billing.record(
                module="image",
                provider="dashscope",
                model=model,
                units={"image_count": image_count},
                ref_type="image_gen",
            )
    except Exception:
        logger.exception("[Billing] 图像计费失败（已忽略）")

    workspace = _resolve_workspace()
    subdir = save_dir.strip().strip("/").strip("\\") if save_dir else "images"
    # 防止路径穿越
    images_dir = (workspace / subdir).resolve()
    if not str(images_dir).startswith(str(workspace.resolve())):
        return "❌ save_dir 路径无效（不允许超出工作区范围）"
    await asyncio.to_thread(images_dir.mkdir, parents=True, exist_ok=True)

    stem = (name or f"qwen-image-{secrets.token_hex(4)}").strip()
    stem = re.sub(r"[^A-Za-z0-9._一-鿿-]", "_", stem)[:80] or "qwen-image"

    desc_parts: list[str] = []
    if prompt:
        desc_parts.append(prompt[:160])
    if result.width and result.height:
        desc_parts.append(f"{result.width}x{result.height}")
    desc_parts.append(model)
    desc = " · ".join(desc_parts)

    lines: list[str] = [f"✅ 已生成 {len(result.images)} 张图像（{model}）"]
    if result.width and result.height:
        lines.append(f"分辨率：{result.width}x{result.height}")
    if result.request_id:
        lines.append(f"request_id：{result.request_id}")

    failures: list[str] = []
    registered: list[tuple[int, str]] = []
    multi = len(result.images) > 1
    for idx, url in enumerate(result.images, start=1):
        suffix = f"-{idx}" if multi else ""
        fallback_stem = f"{stem}{suffix}"
        fname_from_url = _filename_from_url(url, fallback_stem)
        ext = fname_from_url.rsplit(".", 1)[1] if "." in fname_from_url else "png"
        fname = f"{fallback_stem}.{ext}"
        target = images_dir / fname
        try:
            size_bytes = await _download_image(url, target)
            rel_to_project = target.resolve().relative_to(_PROJECT_ROOT.resolve())
            rel_str = str(rel_to_project).replace("\\", "/")
            aid = await _register_image_artifact(
                name=fname,
                rel_to_project=rel_str,
                size=size_bytes,
                description=desc,
            )
            if aid:
                registered.append((aid, fname))
        except Exception as e:
            logger.exception(f"图像下载/登记失败：{url}")
            failures.append(f"图 {idx}：{e}")

    if registered:
        lines.append(f"已登记 {len(registered)} 个 image artifact（对话里前端会自动展示图片卡片，不要在回复文本里贴 URL）")
        # 看板 fileNode 需要产物下载链接：ID 只在这里给出，agent 不得自行猜测/编写产物 ID
        for aid, fname in registered:
            lines.append(f"- artifact#{aid}（{fname}）· 挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{aid}/download?inline=1")
    if failures:
        lines.append("⚠️ 部分图像处理失败：")
        lines.extend(failures)

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# GPT-image-2 Tool
# ══════════════════════════════════════════════════════════════════════════════


@tool
async def generate_gpt_image(
    prompt: Annotated[
        str,
        "正向提示词。文生图建议 80~200 字，详细描述主体/风格/构图/光照；图像编辑时是编辑指令。最大 1000 字符。",
    ],
    images: Annotated[
        Optional[Union[list[str], str]],
        "输入图像（可选，1~多张），用于图像编辑/多图融合。支持 http(s) URL、本地路径、data URI / base64。不传则纯文生图。",
    ] = None,
    n: Annotated[
        int,
        "生成张数，1~10",
    ] = 1,
    size: Annotated[
        Optional[str],
        "输出分辨率，支持 1024x1024、1536x1024（横版）、1024x1536（竖版）、auto（默认）",
    ] = None,
    quality: Annotated[
        Optional[str],
        "图像质量。可选 low、medium、high、auto",
    ] = None,
    format: Annotated[
        Optional[str],
        "返回图片格式。可选 png、jpeg、webp",
    ] = None,
    name: Annotated[
        Optional[str],
        "产物展示名前缀（不含扩展名）。多张时会自动加 -1/-2 后缀；不传按 prompt 截断生成。",
    ] = None,
    save_dir: Annotated[
        Optional[str],
        "图像保存的子目录（相对工作区的路径，如 'frames/ep01'、'output/poster'）。不传默认保存到 'images/'。",
    ] = None,
) -> str:
    """
    用 QuickRouter GPT-image 生成或编辑图像（同步阻塞，一次返回）。具体用哪个模型由平台模型配置决定，无需关心。
    文生图：只传 prompt；图像编辑/多图融合：传 1~多张 images。
    成功后自动登记 image artifact、前端展示图片卡片——不要在文本里贴 URL，简短交代生成内容即可。
    """
    if not has_capability("IMAGE"):
        return "❌ 图像生成不可用：IMAGE 能力未配置（缺 IMAGE_PROVIDER / IMAGE_API_KEY）"

    # 模型一律由平台配置的预设块决定（超管切换的真相源），工具不接受模型覆盖
    model = load_provider("IMAGE").model or gi.DEFAULT_MODEL

    try:
        images_list = _coerce_str_list(images, name="images")
    except ValueError as e:
        return f"❌ {e}"

    try:
        if images_list:
            resolved: list[str] = []
            for ref in images_list:
                local = _resolve_local_path(ref)
                resolved.append(str(local) if local is not None else ref)
            result = await gi.edit(
                image=resolved,
                prompt=prompt,
                model=model,
                n=n,
                size=size,
                quality=quality,
                format=format,
            )
        else:
            result = await gi.generate(
                prompt=prompt,
                model=model,
                n=n,
                size=size,
                quality=quality,
                format=format,
            )
    except gi.GptImageError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.exception("generate_gpt_image 调用异常")
        return f"❌ 调用失败：{e}"

    # ── 计费记账 ──────────────────────────────────────────────────────────────
    try:
        from app.langchain.billing.pricing import Billing

        image_count = len(result.images)
        if image_count > 0:
            await Billing.record(
                module="image",
                provider="quickrouter",
                model=model,
                units={"image_count": image_count},
                ref_type="image_gen",
            )
    except Exception:
        logger.exception("[Billing] 图像计费失败（已忽略）")

    workspace = _resolve_workspace()
    subdir = save_dir.strip().strip("/").strip("\\") if save_dir else "images"
    # 防止路径穿越
    images_dir = (workspace / subdir).resolve()
    if not str(images_dir).startswith(str(workspace.resolve())):
        return "❌ save_dir 路径无效（不允许超出工作区范围）"
    await asyncio.to_thread(images_dir.mkdir, parents=True, exist_ok=True)

    stem = (name or f"gpt-image-{secrets.token_hex(4)}").strip()
    stem = re.sub(r"[^A-Za-z0-9._一-鿿-]", "_", stem)[:80] or "gpt-image"

    desc_parts: list[str] = []
    if prompt:
        desc_parts.append(prompt[:160])
    if result.width and result.height:
        desc_parts.append(f"{result.width}x{result.height}")
    desc_parts.append(model)
    desc = " · ".join(desc_parts)

    lines: list[str] = [f"✅ 已生成 {len(result.images)} 张图像（{model}）"]
    if result.width and result.height:
        lines.append(f"分辨率：{result.width}x{result.height}")
    if result.request_id:
        lines.append(f"request_id：{result.request_id}")

    failures: list[str] = []
    registered: list[tuple[int, str]] = []
    multi = len(result.images) > 1
    for idx, url in enumerate(result.images, start=1):
        suffix = f"-{idx}" if multi else ""
        fallback_stem = f"{stem}{suffix}"
        fname_from_url = _filename_from_url(url, fallback_stem)
        ext = fname_from_url.rsplit(".", 1)[1] if "." in fname_from_url else "png"
        fname = f"{fallback_stem}.{ext}"
        target = images_dir / fname
        try:
            size_bytes = await _download_image(url, target)
            rel_to_project = target.resolve().relative_to(_PROJECT_ROOT.resolve())
            rel_str = str(rel_to_project).replace("\\", "/")
            aid = await _register_image_artifact(
                name=fname,
                rel_to_project=rel_str,
                size=size_bytes,
                description=desc,
            )
            if aid:
                registered.append((aid, fname))
        except Exception as e:
            logger.exception(f"图像下载/登记失败：{url}")
            failures.append(f"图 {idx}：{e}")

    if registered:
        lines.append(f"已登记 {len(registered)} 个 image artifact（对话里前端会自动展示图片卡片，不要在回复文本里贴 URL）")
        # 看板 fileNode 需要产物下载链接：ID 只在这里给出，agent 不得自行猜测/编写产物 ID
        for aid, fname in registered:
            lines.append(f"- artifact#{aid}（{fname}）· 挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{aid}/download?inline=1")
    if failures:
        lines.append("⚠️ 部分图像处理失败：")
        lines.extend(failures)

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# APIPod GPT-image-2 Tool（异步任务式）
# ══════════════════════════════════════════════════════════════════════════════


@tool
async def generate_apipod_image(
    prompt: Annotated[
        str,
        "正向提示词。文生图建议 80~200 字，详细描述主体/风格/构图/光照；图像编辑时是编辑指令。",
    ],
    images: Annotated[
        Optional[Union[list[str], str]],
        "参考图像（可选，最多 16 张），用于图像编辑/多图融合。支持 http(s)、data:base64、工作区相对路径（自动换成公网 URL）。不传则纯文生图。",
    ] = None,
    aspect_ratio: Annotated[
        Optional[str],
        "输出宽高比：auto / 1:1 / 2:3 / 3:2 / 3:4 / 4:3 / 4:5 / 5:4 / 9:16 / 16:9 / 21:9",
    ] = None,
    resolution: Annotated[
        Optional[str],
        "输出分辨率：1K / 2K / 4K，越高越清晰也越贵",
    ] = None,
    quality: Annotated[
        Optional[str],
        "图像质量：auto / low / medium / high",
    ] = None,
    name: Annotated[
        Optional[str],
        "产物展示名前缀（不含扩展名）",
    ] = None,
    save_dir: Annotated[
        Optional[str],
        "图像保存子目录（工作区相对路径），默认 'images/'",
    ] = None,
) -> str:
    """
    用 APIPod 中转的 GPT-image-2 生成或编辑图像（异步任务式，内部轮询直到出图）。
    文生图：只传 prompt；图像编辑/多图融合：传 1~16 张 images 参考图。
    成功后自动登记 image artifact、前端展示图片卡片——不要在文本里贴 URL，简短交代生成内容即可。
    """
    if not has_capability("IMAGE"):
        return "❌ 图像生成不可用：IMAGE 能力未配置（缺 IMAGE_PROVIDER / IMAGE_API_KEY）"

    model = load_provider("IMAGE").model or api.DEFAULT_MODEL

    try:
        images_list = _coerce_str_list(images, name="images")
    except ValueError as e:
        return f"❌ {e}"

    # APIPod 参考图必须是公网 URL：http(s) 直通，data:/本地路径经视频侧同款机制换公网 URL
    if images_list:
        from app.langchain.tools.video_tools import _image_to_public_url

        public_urls: list[str] = []
        for ref in images_list:
            try:
                public_urls.append(await _image_to_public_url(ref))
            except ValueError as e:
                return f"❌ 参考图像处理失败：{e}"
            except Exception as e:
                logger.exception("参考图归一化异常")
                return f"❌ 参考图像处理失败：{e}"
        images_list = public_urls

    try:
        submit = await api.submit_task(
            prompt=prompt,
            image_urls=images_list,
            aspect_ratio=aspect_ratio or "auto",
            resolution=resolution or "1K",
            quality=quality or "auto",
            model=model,
        )
        result = await api.wait_task(submit.task_id)
    except api.ApipodImageError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.exception("generate_apipod_image 调用异常")
        return f"❌ 调用失败：{e}"

    if result["status"] != "completed" or not result["images"]:
        err = result.get("error_message") or f"任务状态：{result['status']}"
        return f"❌ APIPod 图像生成失败：{err}"

    # ── 计费记账 ──────────────────────────────────────────────────────────────
    try:
        from app.langchain.billing.pricing import Billing

        image_count = len(result["images"])
        if image_count > 0:
            await Billing.record(
                module="image",
                provider="apipod",
                model=model,
                units={"image_count": image_count},
                ref_type="image_gen",
            )
    except Exception:
        logger.exception("[Billing] 图像计费失败（已忽略）")

    workspace = _resolve_workspace()
    subdir = save_dir.strip().strip("/").strip("\\") if save_dir else "images"
    # 防止路径穿越
    images_dir = (workspace / subdir).resolve()
    if not str(images_dir).startswith(str(workspace.resolve())):
        return "❌ save_dir 路径无效（不允许超出工作区范围）"
    await asyncio.to_thread(images_dir.mkdir, parents=True, exist_ok=True)

    stem = (name or f"gpt-image-{secrets.token_hex(4)}").strip()
    stem = re.sub(r"[^A-Za-z0-9._一-鿿-]", "_", stem)[:80] or "gpt-image"

    desc_parts: list[str] = []
    if prompt:
        desc_parts.append(prompt[:160])
    if resolution:
        desc_parts.append(resolution)
    desc_parts.append(model)
    desc = " · ".join(desc_parts)

    lines: list[str] = [f"✅ 已生成 {len(result['images'])} 张图像（{model}）"]

    failures: list[str] = []
    registered: list[tuple[int, str]] = []
    multi = len(result["images"]) > 1
    for idx, url in enumerate(result["images"], start=1):
        suffix = f"-{idx}" if multi else ""
        fallback_stem = f"{stem}{suffix}"
        fname_from_url = _filename_from_url(url, fallback_stem)
        ext = fname_from_url.rsplit(".", 1)[1] if "." in fname_from_url else "png"
        fname = f"{fallback_stem}.{ext}"
        target = images_dir / fname
        try:
            size_bytes = await _download_image(url, target)
            rel_to_project = target.resolve().relative_to(_PROJECT_ROOT.resolve())
            rel_str = str(rel_to_project).replace("\\", "/")
            aid = await _register_image_artifact(
                name=fname,
                rel_to_project=rel_str,
                size=size_bytes,
                description=desc,
            )
            if aid:
                registered.append((aid, fname))
        except Exception as e:
            logger.exception(f"图像下载/登记失败：{url}")
            failures.append(f"图 {idx}：{e}")

    if registered:
        lines.append(f"已登记 {len(registered)} 个 image artifact（对话里前端会自动展示图片卡片，不要在回复文本里贴 URL）")
        # 看板 fileNode 需要产物下载链接：ID 只在这里给出，agent 不得自行猜测/编写产物 ID
        for aid, fname in registered:
            lines.append(f"- artifact#{aid}（{fname}）· 挂工作流看板 fileNode 的 url：/ai/agent/artifacts/{aid}/download?inline=1")
    if failures:
        lines.append("⚠️ 部分图像处理失败：")
        lines.extend(failures)

    return "\n".join(lines)


# ── 能力开关：按 IMAGE_PROVIDER 返回当前启用的图像工具 ──────────────────────


def get_image_tools() -> list[Any]:
    """返回当前启用的图像生成工具；IMAGE 能力未配置返回空列表。

    IMAGE_PROVIDER=gpt → QuickRouter GPT-image-2；apipod → APIPod GPT-image-2；
    其它值（qwen 等）→ Qwen-Image。
    """
    if not has_capability("IMAGE"):
        return []
    provider = load_provider("IMAGE").provider
    if provider == "gpt":
        return [generate_gpt_image]
    if provider == "apipod":
        return [generate_apipod_image]
    return [generate_qwen_image]
