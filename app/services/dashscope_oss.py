"""
DashScope 临时 OSS 上传客户端：把本地文件转成 oss:// 临时 URL，
喂给图像编辑 / 图生视频 / 多模态理解等接口。

官方限制：
  - URL 有效期 48 小时
  - 上传时指定的 model 必须等于后续调用的 model
  - 100 QPS（按"主账号 + 模型"维度）
  - 调用模型时 HTTP 必须加 X-DashScope-OssResourceResolve: enable

策略：
  - 不缓存 policy（5 分钟过期，缓存收益小）
  - 缓存"文件 sha256 + model → oss URI"，TTL 24 小时（官方上限 48h 留一半）
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger

from app.langchain.config import langchain_config

# DashScope OSS Resource Resolve header 名 / 值。所有要消费 oss:// URI 的模型调用都要带。
OSS_RESOLVE_HEADER = "X-DashScope-OssResourceResolve"
OSS_RESOLVE_VALUE = "enable"

_POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"

# 缓存 TTL：官方 48h，留一半余量
_CACHE_TTL_SEC = 24 * 3600

# (sha256, model) -> (oss_uri, expire_at_epoch)
_uri_cache: dict[tuple[str, str], tuple[str, float]] = {}
_cache_lock = asyncio.Lock()


class DashScopeOssError(RuntimeError):
    """DashScope OSS 上传错误。"""


def _api_key() -> str:
    key = langchain_config.DASHSCOPE_API_KEY
    if not key:
        raise DashScopeOssError("DASHSCOPE_API_KEY 未配置")
    return key


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _get_policy(model: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """GET /api/v1/uploads?action=getPolicy&model=...，返回 data 字段。"""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    params = {"action": "getPolicy", "model": model}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(_POLICY_URL, headers=headers, params=params)
    if resp.status_code >= 400:
        raise DashScopeOssError(
            f"getPolicy 失败 HTTP {resp.status_code}：{resp.text[:300]}"
        )
    body = resp.json()
    data = body.get("data")
    if not isinstance(data, dict):
        raise DashScopeOssError(f"getPolicy 响应缺少 data 字段：{body}")
    required = (
        "policy", "signature", "upload_dir", "upload_host",
        "oss_access_key_id", "x_oss_object_acl", "x_oss_forbid_overwrite",
    )
    missing = [k for k in required if k not in data]
    if missing:
        raise DashScopeOssError(f"getPolicy 响应字段缺失：{missing}，原始：{data}")
    return data


def _check_size(local_path: Path, policy: dict[str, Any]) -> None:
    """对照 policy.max_file_size_mb 校验文件大小。"""
    raw = policy.get("max_file_size_mb")
    if raw is None:
        return
    try:
        max_bytes = int(raw) * 1024 * 1024
    except (TypeError, ValueError):
        return
    actual = local_path.stat().st_size
    if actual > max_bytes:
        raise DashScopeOssError(
            f"文件 {local_path.name} 太大 ({actual} bytes)，"
            f"超过 model 限制 {raw} MB"
        )


async def _upload_file(local_path: Path, policy: dict[str, Any], *, timeout: float = 300.0) -> str:
    """multipart POST 到 upload_host，返回 oss://{key}。"""
    file_name = local_path.name
    key = f"{policy['upload_dir']}/{file_name}"

    # data 字段（除 file 外的全部文本字段）— httpx 先写 data 再写 files，正好满足
    # "file 必须是 multipart 最后一个字段" 的官方约束
    data = {
        "OSSAccessKeyId": policy["oss_access_key_id"],
        "Signature": policy["signature"],
        "policy": policy["policy"],
        "x-oss-object-acl": policy["x_oss_object_acl"],
        "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
        "key": key,
        "success_action_status": "200",
    }
    file_bytes = await asyncio.to_thread(local_path.read_bytes)
    files = {"file": (file_name, file_bytes)}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(policy["upload_host"], data=data, files=files)
    if resp.status_code != 200:
        raise DashScopeOssError(
            f"上传失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    return f"oss://{key}"


async def upload_for_dashscope(local_path: str | Path, *, model: str) -> str:
    """把本地文件上传到 DashScope OSS，返回 oss://... 临时 URL。

    Args:
        local_path: 本地绝对路径（调用方负责把工作区相对路径解析为绝对路径再传进来）
        model:      目标模型名（必须与后续模型调用一致，否则不识别）

    Returns:
        形如 `oss://dashscope-instant/xxx/2024-07-18/xxx/cat.png` 的临时 URL，
        24 小时缓存命中可重用，超出 TTL 重新上传。
    """
    p = Path(local_path)
    if not p.is_absolute():
        raise DashScopeOssError(f"local_path 必须是绝对路径，得到 {local_path!r}")
    if not await asyncio.to_thread(p.is_file):
        raise DashScopeOssError(f"文件不存在：{p}")

    sha = await asyncio.to_thread(_sha256_of, p)
    cache_key = (sha, model)
    now = time.time()

    async with _cache_lock:
        hit = _uri_cache.get(cache_key)
        if hit is not None and hit[1] > now:
            logger.debug(f"DashScope OSS cache hit: {p.name} model={model}")
            return hit[0]

    policy = await _get_policy(model)
    _check_size(p, policy)
    oss_uri = await _upload_file(p, policy)
    logger.info(f"DashScope OSS uploaded: {p.name} model={model} -> {oss_uri}")

    async with _cache_lock:
        _uri_cache[cache_key] = (oss_uri, now + _CACHE_TTL_SEC)

    return oss_uri


def is_remote_uri(s: Optional[str]) -> bool:
    """判断字符串是否是已经可以直接喂给模型的远端引用。"""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return s.startswith(("http://", "https://", "oss://", "data:"))


__all__ = [
    "DashScopeOssError",
    "OSS_RESOLVE_HEADER",
    "OSS_RESOLVE_VALUE",
    "upload_for_dashscope",
    "is_remote_uri",
]
