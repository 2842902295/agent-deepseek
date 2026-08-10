"""
GPT-image-2 图像生成与编辑 API 客户端（QuickRouter 中转）。

  - 文生图：POST /v1/images/generations，传 prompt
  - 图像编辑：POST /v1/images/edits，传 image（base64）+ prompt

凭据与端点来自 load_provider("IMAGE")：IMAGE_API_KEY / IMAGE_BASE_URL。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import httpx
from loguru import logger

from app.langchain.config import load_provider

# ── Endpoints ─────────────────────────────────────────────────────────────────
_DEFAULT_BASE = "https://api.quickrouter.ai/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
MODEL_GPT_IMAGE_2 = "gpt-image-2"
DEFAULT_MODEL = MODEL_GPT_IMAGE_2


class GptImageError(RuntimeError):
    """GPT-Image 调用错误。"""


@dataclass
class ImageGenResult:
    """gpt-image 调用结果。"""

    images: list[str]                 # 一张或多张图像 URL
    width: Optional[int] = None
    height: Optional[int] = None
    image_count: int = 0
    request_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


def _base() -> str:
    return (load_provider("IMAGE").base_url or _DEFAULT_BASE).rstrip("/")


def _api_key() -> str:
    return load_provider("IMAGE").api_key


def _parse_size(size: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not size or size == "auto":
        return None, None
    try:
        parts = size.lower().replace("*", "x").split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass
    return None, None


def _normalize_size(size: Optional[str]) -> str:
    """将 * 分隔的尺寸统一为 x 分隔，空值退回 auto。"""
    if not size:
        return "auto"
    return size.replace("*", "x")


def _parse_error(resp: httpx.Response) -> GptImageError:
    try:
        data = resp.json()
        err = data.get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", resp.text[:300])
            code = err.get("code", "")
        else:
            msg = str(err) or resp.text[:300]
            code = ""
        return GptImageError(f"GPT-Image 失败 HTTP {resp.status_code} · {code}：{msg}")
    except Exception:
        return GptImageError(f"GPT-Image 失败 HTTP {resp.status_code}：{resp.text[:500]}")


def _extract_images(data: dict[str, Any]) -> list[str]:
    """从响应 JSON 中提取图像 URL/base64 列表。

    支持两种格式：
    1. OpenAI 标准格式：data[{url/b64_json}]
    2. 兼容格式：choices[{message: {content}}]（之前的错误假设）
    """
    images_out: list[str] = []

    # 优先尝试标准 OpenAI 格式：data[]
    if "data" in data:
        data_list = data.get("data") or []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            # url 优先，没有 url 就取 b64_json 转 data URI
            if url := item.get("url"):
                images_out.append(url)
            elif b64 := item.get("b64_json"):
                images_out.append(f"data:image/png;base64,{b64}")
        if images_out:
            return images_out

    # 兼容之前的 choices[] 格式（如果有的话）
    choices = data.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, str):
            images_out.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    url = item.get("url") or item.get("image") or (item.get("image_url") or {}).get("url")
                    if url:
                        images_out.append(url)
                elif isinstance(item, str):
                    images_out.append(item)

    if not images_out:
        raise GptImageError(f"GPT-Image 响应未返回图像：{data}")
    return images_out


async def _to_bytes(image_ref: str) -> bytes:
    """
    将图像引用转为 bytes。
    支持：
      - data:image/... URI → base64 解码
      - 本地文件路径 → 直接读取
      - http(s) URL → 下载
      - 纯 base64 字符串 → 解码
    """
    if image_ref.startswith("data:"):
        # data URI，提取 base64 部分并解码
        if "," in image_ref:
            b64_data = image_ref.split(",", 1)[1]
            return base64.b64decode(b64_data)
        raise GptImageError(f"data URI 格式错误：{image_ref[:100]}")
    # 本地文件
    p = Path(image_ref)
    if p.is_file():
        return p.read_bytes()
    # http(s) URL
    if image_ref.startswith("http"):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(image_ref)
            resp.raise_for_status()
            return resp.content
    # 看起来像纯 base64
    if not Path(image_ref).suffix:
        try:
            return base64.b64decode(image_ref)
        except Exception:
            pass
    raise GptImageError(f"无法处理图像引用：{image_ref!r}")


async def _to_base64(image_ref: str) -> str:
    """将图像引用转为 base64 字符串（用于 JSON payload）。"""
    return base64.b64encode(await _to_bytes(image_ref)).decode()


async def generate(
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    n: int = 1,
    size: Optional[str] = None,
    quality: Optional[str] = None,
    format: Optional[str] = None,
    timeout: float = 180.0,
) -> ImageGenResult:
    """调用 GPT-image-2 文生图接口。"""
    if not prompt or not prompt.strip():
        raise GptImageError("prompt 不能为空")
    n = max(1, min(10, n))
    normalized_size = _normalize_size(size)

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt[:1000],
        "n": int(n),
        "size": normalized_size,
    }
    if quality:
        payload["quality"] = quality
    if format:
        payload["format"] = format

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_base()}/images/generations", headers=headers, json=payload)

    if resp.status_code >= 400:
        raise _parse_error(resp)

    data = resp.json()
    images_out = _extract_images(data)
    width, height = _parse_size(normalized_size)
    return ImageGenResult(
        images=images_out,
        width=width,
        height=height,
        image_count=len(images_out),
        request_id=data.get("id"),
        raw=data,
    )


async def edit(
    *,
    image: Union[str, list[str]],
    prompt: str,
    model: str = DEFAULT_MODEL,
    mask: Optional[str] = None,
    n: int = 1,
    size: Optional[str] = None,
    quality: Optional[str] = None,
    format: Optional[str] = None,
    timeout: float = 180.0,
) -> ImageGenResult:
    """
    调用 GPT-image-2 图像编辑接口（multipart/form-data）。

    Args:
        image: 原始图像，支持单张或多张（list）；每张可为 http(s) URL、本地文件路径、data URI 或 base64 字符串
        prompt: 编辑指令，最大 32000 字符
        mask: 蒙版图像（可选），透明区域表示待编辑区域，应用于第一张图
        model: 模型 ID
        n: 生成数量，1~10
        size: 尺寸，支持 1024x1024、1536x1024、1024x1536、auto
        quality: 质量，可选 low、medium、high、auto
        format: 格式，可选 png、jpeg、webp
        timeout: 请求超时
    """
    if not prompt or not prompt.strip():
        raise GptImageError("prompt 不能为空")
    n = max(1, min(10, n))
    normalized_size = _normalize_size(size)

    image_refs = [image] if isinstance(image, str) else image

    def _ext_mime(ref: str) -> tuple[str, str]:
        ext = Path(ref.split("?")[0]).suffix.lstrip(".").lower() or "png"
        if ext not in {"png", "jpg", "jpeg", "webp"}:
            ext = "png"
        mime = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext}"
        return ext, mime

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Accept": "application/json",
    }
    data: dict[str, Any] = {
        "model": model,
        "prompt": prompt[:32000],
        "n": str(n),
        "size": normalized_size,
    }
    if quality:
        data["quality"] = quality
    if format:
        data["format"] = format

    # httpx 用 list[tuple] 传同名多字段
    files: list[tuple[str, Any]] = []
    for idx, ref in enumerate(image_refs):
        img_bytes = await _to_bytes(ref)
        ext, mime = _ext_mime(ref)
        files.append(("image", (f"image{idx}.{ext}", img_bytes, mime)))

    if mask:
        mask_bytes = await _to_bytes(mask)
        mask_ext, mask_mime = _ext_mime(mask)
        files.append(("mask", (f"mask.{mask_ext}", mask_bytes, mask_mime)))

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{_base()}/images/edits", headers=headers, data=data, files=files)

    if resp.status_code >= 400:
        raise _parse_error(resp)

    data_resp = resp.json()
    images_out = _extract_images(data_resp)
    width, height = _parse_size(normalized_size)
    return ImageGenResult(
        images=images_out,
        width=width,
        height=height,
        image_count=len(images_out),
        request_id=data_resp.get("id"),
        raw=data_resp,
    )


__all__ = [
    "GptImageError",
    "ImageGenResult",
    "MODEL_GPT_IMAGE_2",
    "DEFAULT_MODEL",
    "generate",
    "edit",
]
