"""
千问图像生成与编辑（Qwen-Image-2.0 Pro）同步 API 客户端。

走 multimodal-generation/generation 接口（北京区）：
  - 文生图：messages[0].content = [{"text": "..."}]
  - 图像编辑/多图融合：messages[0].content = [{"image": url|base64}, ..., {"text": "..."}]
                       1~3 张 image，最后一张决定输出宽高比

凭据与端点来自 load_provider("IMAGE")：IMAGE_API_KEY / IMAGE_BASE_URL。
同步接口本身就阻塞，无需轮询。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx
from loguru import logger

from app.langchain.config import load_provider
from app.services.dashscope_oss import OSS_RESOLVE_HEADER, OSS_RESOLVE_VALUE

# ── Endpoint（北京区） ───────────────────────────────────────────────────────
_DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
MODEL_PRO = "qwen-image-2.0-pro"
MODEL_FLASH = "qwen-image-2.0"          # Pro 加速版
MODEL_MAX = "qwen-image-max"            # 文生图 Max（n 固定 1）
MODEL_PLUS = "qwen-image-plus"          # 文生图 Plus（n 固定 1）
MODEL_EDIT_MAX = "qwen-image-edit-max"  # 编辑 Max
DEFAULT_MODEL = MODEL_PRO

# qwen-image-2.0 系列 / edit-max / edit-plus 系列：n 可 1~6；其它系列 n 固定 1
_N_FREE_MODELS_PREFIX = ("qwen-image-2.0", "qwen-image-edit-max", "qwen-image-edit-plus")


class QwenImageError(RuntimeError):
    """Qwen-Image 调用错误。"""


@dataclass
class ImageGenResult:
    """qwen-image 调用结果。"""

    images: list[str]                 # 一张或多张图像 URL
    width: Optional[int] = None
    height: Optional[int] = None
    image_count: int = 0
    request_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


def _api_url() -> str:
    base = (load_provider("IMAGE").base_url or _DEFAULT_BASE).rstrip("/")
    return f"{base}/services/aigc/multimodal-generation/generation"


def _api_key() -> str:
    return load_provider("IMAGE").api_key


def _supports_multi_image(model: str) -> bool:
    return any(model.startswith(p) for p in _N_FREE_MODELS_PREFIX)


def build_messages(
    prompt: str,
    images: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """构造 multimodal-generation messages。

    images 为 None / 空列表 → 文生图，content = [{text: prompt}]
    否则 → 图像编辑/融合，content = [{image: url|base64}, ..., {text: prompt}]
    """
    if not prompt or not prompt.strip():
        raise QwenImageError("prompt 不能为空")
    content: list[dict[str, Any]] = []
    if images:
        if len(images) > 3:
            raise QwenImageError(f"输入图像最多 3 张，得到 {len(images)} 张")
        for url in images:
            if not isinstance(url, str) or not url.strip():
                raise QwenImageError(f"图像引用无效：{url!r}")
            content.append({"image": url.strip()})
    content.append({"text": prompt})
    return [{"role": "user", "content": content}]


async def generate(
    *,
    prompt: str,
    images: Optional[list[str]] = None,
    model: str = DEFAULT_MODEL,
    n: int = 1,
    size: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    prompt_extend: Optional[bool] = None,
    watermark: bool = False,
    seed: Optional[int] = None,
    timeout: float = 180.0,
) -> ImageGenResult:
    """同步调用 Qwen-Image 生成图像。"""
    if n < 1:
        n = 1
    if not _supports_multi_image(model) and n != 1:
        # max / plus 系列固定 n=1，传别的会报错；这里直接收口，避免无谓失败。
        n = 1
    if n > 6:
        n = 6

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        # 让 oss:// URI 在内部解析；不消费 oss URI 时多带这个头也不影响。
        OSS_RESOLVE_HEADER: OSS_RESOLVE_VALUE,
    }
    parameters: dict[str, Any] = {"watermark": bool(watermark), "n": int(n)}
    if size:
        parameters["size"] = size
    if negative_prompt is not None:
        parameters["negative_prompt"] = negative_prompt
    if prompt_extend is not None:
        parameters["prompt_extend"] = bool(prompt_extend)
    if seed is not None:
        parameters["seed"] = int(seed)

    payload = {
        "model": model,
        "input": {"messages": build_messages(prompt, images=images)},
        "parameters": parameters,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_api_url(), headers=headers, json=payload)

    if resp.status_code >= 400:
        # 失败：尝试解析平台错误信息，便于 LLM/用户读
        try:
            data = resp.json()
            code = data.get("code")
            msg = data.get("message")
            raise QwenImageError(
                f"Qwen-Image 失败 HTTP {resp.status_code} · {code or ''}：{msg or resp.text[:300]}"
            )
        except QwenImageError:
            raise
        except Exception:
            raise QwenImageError(
                f"Qwen-Image 失败 HTTP {resp.status_code}：{resp.text[:500]}"
            )

    data = resp.json()
    output = data.get("output") or {}
    choices = output.get("choices") or []
    if not choices:
        raise QwenImageError(f"Qwen-Image 响应缺少 choices：{data}")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or []
    images_out: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("image"):
            images_out.append(item["image"])
    if not images_out:
        raise QwenImageError(f"Qwen-Image 响应未返回图像：{data}")

    usage = data.get("usage") or {}
    return ImageGenResult(
        images=images_out,
        width=usage.get("width"),
        height=usage.get("height"),
        image_count=usage.get("image_count") or len(images_out),
        request_id=data.get("request_id"),
        raw=data,
    )


__all__ = [
    "QwenImageError",
    "ImageGenResult",
    "MODEL_PRO",
    "MODEL_FLASH",
    "MODEL_MAX",
    "MODEL_PLUS",
    "MODEL_EDIT_MAX",
    "DEFAULT_MODEL",
    "build_messages",
    "generate",
]
