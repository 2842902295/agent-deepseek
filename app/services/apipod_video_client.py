"""
APIPod（Grok Imagine 1.5）视频生成异步 API 客户端。

只做协议层翻译：
  - submit_task：POST /videos/generations 创建任务，返回 (task_id, raw)
  - query_task ：GET  /videos/status/{task_id} 轮询任务，返回标准化 dict
  - wait_task  ：在客户端循环 query_task 直至终态，便于工具同步使用

凭据与端点来自 load_provider("VIDEO")：VIDEO_APIPOD_PROVIDER / API_KEY / MODEL / BASE_URL
（激活块重定向机制：超管在切换页选中 VIDEO_APIPOD 后，load_provider("VIDEO") 会重定向到该块）。

与其它 client 的关键差异：
  - 响应是统一信封 {code, message, data}：HTTP 200 也可能是业务失败（code != 200，
    402 = 余额不足 / 429 = 限流 / 401 = 凭据无效），必须同时检查 HTTP 状态与 body code
  - Grok Imagine 1.5 Preview 是纯图生视频（I2V）模型：image_url 必填且仅支持单张图，
    resolution ∈ {480p（默认，省钱）, 720p}，duration 1~15 秒，aspect_ratio ∈ {1:1, 2:3, 3:2, 9:16, 16:9}
  - 轮询节奏按官方建议自适应：前 30s 每 3s，30s~2min 每 8s，之后每 20s
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from loguru import logger

from app.langchain.config import load_provider

# ── Endpoints ────────────────────────────────────────────────────────────────
_DEFAULT_BASE = "https://api.apipod.ai/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
MODEL_GROK_IMAGINE_1_5_PREVIEW = "grok-imagine-1.5-preview"
# VIDEO_APIPOD_MODEL 留空时的默认
DEFAULT_MODEL = MODEL_GROK_IMAGINE_1_5_PREVIEW

SUPPORTED_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "9:16", "16:9")
SUPPORTED_RESOLUTIONS = ("480p", "720p")  # 480p 最省钱（默认）；720p 画质更高

# ── 终态集合 ─────────────────────────────────────────────────────────────────
TERMINAL_STATES = {"completed", "failed", "cancelled"}


class ApipodVideoError(RuntimeError):
    """APIPod 视频生成调用错误。"""


@dataclass
class ApipodSubmitResult:
    task_id: str
    raw: dict[str, Any]


def _api_base() -> str:
    return (load_provider("VIDEO").base_url or _DEFAULT_BASE).rstrip("/")


def _api_key() -> str:
    return load_provider("VIDEO").api_key


def _model() -> str:
    return load_provider("VIDEO").model or DEFAULT_MODEL


async def submit_task(
    *,
    prompt: str,
    image_url: str,
    aspect_ratio: str = "16:9",
    duration: int = 10,
    resolution: str = "480p",
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> ApipodSubmitResult:
    """创建视频生成任务，返回 task_id。

    Args:
        prompt:       运动描述（≤4000 字），必填
        image_url:    首帧参考图公网 URL，必填（仅支持单张）
        aspect_ratio: 1:1 / 2:3 / 3:2 / 9:16 / 16:9，默认 16:9
        duration:     时长 1~15 秒，默认 10
        resolution:   480p（默认，省钱）/ 720p
        model:        模型 id，留空走 VIDEO_APIPOD_MODEL / 内置默认
    """
    selected_model = model or _model()
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ApipodVideoError(
            f"不支持的 aspect_ratio：{aspect_ratio}，可选 {SUPPORTED_ASPECT_RATIOS}"
        )
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ApipodVideoError(
            f"不支持的 resolution：{resolution}，可选 {SUPPORTED_RESOLUTIONS}"
        )
    payload = {
        "model": selected_model,
        "prompt": prompt,
        "image_url": image_url,
        "aspect_ratio": aspect_ratio,
        "duration": int(duration),
        "resolution": resolution,
    }
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    # trust_env=False：不读取系统代理。本机 Clash 等系统代理（127.0.0.1:7890）会把到
    # api.apipod.ai 的 CONNECT+TLS 隧道掐断（EndOfStream，100% 失败），而该服务可直连；
    # 生产 Docker 无代理，行为不变
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(f"{_api_base()}/videos/generations", headers=headers, json=payload)

    if resp.status_code >= 400:
        raise ApipodVideoError(
            f"APIPod 视频任务提交失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    # 统一信封：{code, message, data:{task_id}}；HTTP 200 也可能是业务失败
    if data.get("code") != 200:
        raise ApipodVideoError(
            f"APIPod 提交失败 code={data.get('code')}：{data.get('message') or resp.text[:300]}"
        )
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise ApipodVideoError(f"APIPod 响应缺少 data.task_id：{data}")
    return ApipodSubmitResult(task_id=task_id, raw=data)


async def query_task(task_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """查询任务状态，返回结构化字典：

    {
        "task_id": str,
        "status": "pending/processing/completed/failed/cancelled/unknown",
        "video_url": Optional[str],      # completed 时取 result[0]
        "result": Optional[list[str]],   # 生成的内容 URL 数组（24h 内有效）
        "completed_at": Optional[int],
        "error_message": Optional[str],
        "raw": dict,                     # 原始响应，便于排查
    }
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = f"{_api_base()}/videos/status/{task_id}"
    # trust_env=False：同 submit_task，绕开掐断 TLS 的系统代理
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise ApipodVideoError(
            f"APIPod 任务查询失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    if data.get("code") != 200:
        raise ApipodVideoError(
            f"APIPod 查询失败 code={data.get('code')}：{data.get('message')}"
        )
    d = data.get("data") or {}
    result = d.get("result")
    video_url: Optional[str] = None
    if isinstance(result, list) and result and isinstance(result[0], str):
        video_url = result[0]
    status = (d.get("status") or "unknown").lower()
    if status == "completed" and not video_url:
        logger.warning(
            f"APIPod 任务 {task_id} 已完成但 result 为空；raw={data}"
        )
    return {
        "task_id": d.get("task_id") or task_id,
        "status": status,
        "video_url": video_url,
        "result": result,
        "completed_at": d.get("completed_at"),
        "error_message": d.get("error"),
        "raw": data,
    }


def _poll_delay(elapsed: float) -> float:
    """官方建议节奏：前 30s 每 2~3s；30s~2min 每 5~10s；2min 后每 15~30s。"""
    if elapsed < 30:
        return 3.0
    if elapsed < 120:
        return 8.0
    return 20.0


async def wait_task(
    task_id: str,
    *,
    max_wait: float = 720.0,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """轮询直到终态或超时（间隔按 _poll_delay 自适应）。

    Args:
        max_wait:    最长等待秒（默认 12 分钟）
        on_progress: 每次拿到非终态结果后回调，async 或同步均可
    """
    elapsed = 0.0
    while True:
        result = await query_task(task_id)
        status = result["status"]
        if status in TERMINAL_STATES:
            return result
        if on_progress is not None:
            try:
                ret = on_progress(result)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:
                logger.debug("on_progress 回调异常（已忽略）", exc_info=True)
        if elapsed >= max_wait:
            result["status"] = "timeout"
            result["error_message"] = f"轮询超时（{max_wait}s）；APIPod 任务 {task_id} 仍未完成"
            return result
        delay = _poll_delay(elapsed)
        await asyncio.sleep(delay)
        elapsed += delay


__all__ = [
    "ApipodVideoError",
    "ApipodSubmitResult",
    "MODEL_GROK_IMAGINE_1_5_PREVIEW",
    "DEFAULT_MODEL",
    "SUPPORTED_ASPECT_RATIOS",
    "SUPPORTED_RESOLUTIONS",
    "TERMINAL_STATES",
    "submit_task",
    "query_task",
    "wait_task",
]
