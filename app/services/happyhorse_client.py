"""
HappyHorse（阿里云百炼视频生成）异步 API 客户端。

只做协议层翻译：
  - submit_task：POST 创建任务，返回 (task_id, request_id)
  - query_task ：GET 轮询任务，返回标准化的 dict
  - wait_task  ：在客户端循环 query_task 直至终态，便于工具同步使用

凭据与端点来自 load_provider("VIDEO")：VIDEO_API_KEY / VIDEO_BASE_URL；
地域默认北京（默认调度区域，保持与 .env 中 EMBED/DashScope MCP 一致，避免跨区调用失败）。

价格表来自官方 2026-05 文档，单位元/秒。失败任务 DashScope 不计费。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import httpx
from loguru import logger

from app.langchain.config import load_provider
from app.services.dashscope_oss import OSS_RESOLVE_HEADER, OSS_RESOLVE_VALUE

# ── Endpoints（北京区） ───────────────────────────────────────────────────────
_DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
MODEL_T2V = "happyhorse-1.0-t2v"
MODEL_I2V = "happyhorse-1.0-i2v"
MODEL_R2V = "happyhorse-1.0-r2v"
MODEL_VIDEO_EDIT = "happyhorse-1.0-video-edit"
SUPPORTED_MODELS = (MODEL_T2V, MODEL_I2V, MODEL_R2V, MODEL_VIDEO_EDIT)

# ── 价格（元/秒） ────────────────────────────────────────────────────────────
PRICE_PER_SEC = {
    "720P": Decimal("0.90"),
    "720": Decimal("0.90"),
    "1080P": Decimal("1.60"),
    "1080": Decimal("1.60"),
}

# ── 终态集合 ─────────────────────────────────────────────────────────────────
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


class HappyHorseError(RuntimeError):
    """HappyHorse 调用错误。"""


@dataclass
class SubmitResult:
    task_id: str
    request_id: Optional[str]
    raw: dict[str, Any]


def estimate_cost(resolution: Optional[str], duration_sec: Optional[int]) -> Optional[Decimal]:
    """根据分辨率和秒数估算费用。任一参数缺失返回 None。"""
    if not resolution or not duration_sec:
        return None
    key = str(resolution).upper().strip()
    price = PRICE_PER_SEC.get(key) or PRICE_PER_SEC.get(key.rstrip("P"))
    if price is None:
        return None
    return (price * Decimal(int(duration_sec))).quantize(Decimal("0.0001"))


def _api_base() -> str:
    return (load_provider("VIDEO").base_url or _DEFAULT_BASE).rstrip("/")


def _submit_url() -> str:
    return f"{_api_base()}/services/aigc/video-generation/video-synthesis"


def _query_url(task_id: str) -> str:
    return f"{_api_base()}/tasks/{task_id}"


def _api_key() -> str:
    return load_provider("VIDEO").api_key


def _build_payload(
    *,
    model: str,
    prompt: Optional[str],
    media: Optional[list[dict[str, Any]]],
    parameters: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if model not in SUPPORTED_MODELS:
        raise HappyHorseError(
            f"不支持的视频模型：{model}，可选 {SUPPORTED_MODELS}"
        )
    input_obj: dict[str, Any] = {}
    if prompt:
        input_obj["prompt"] = prompt
    if media:
        input_obj["media"] = media
    payload: dict[str, Any] = {"model": model, "input": input_obj}
    if parameters:
        payload["parameters"] = parameters
    return payload


async def submit_task(
    *,
    model: str,
    prompt: Optional[str] = None,
    media: Optional[list[dict[str, Any]]] = None,
    parameters: Optional[dict[str, Any]] = None,
    timeout: float = 60.0,
) -> SubmitResult:
    """创建视频生成任务，返回 task_id。"""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        # 让 oss:// URI 在内部解析；不消费 oss URI 时多带这个头也不影响，
        # 提前加上避免 i2v/r2v/video-edit 漏配。
        OSS_RESOLVE_HEADER: OSS_RESOLVE_VALUE,
    }
    payload = _build_payload(model=model, prompt=prompt, media=media, parameters=parameters)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_submit_url(), headers=headers, json=payload)

    if resp.status_code >= 400:
        raise HappyHorseError(
            f"HappyHorse 提交失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    output = data.get("output") or {}
    task_id = output.get("task_id")
    if not task_id:
        raise HappyHorseError(f"HappyHorse 响应缺少 task_id：{data}")
    return SubmitResult(
        task_id=task_id,
        request_id=data.get("request_id"),
        raw=data,
    )


async def query_task(task_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """查询任务状态，返回结构化字典：

    {
        "task_id": str,
        "status": "PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN",
        "video_url": Optional[str],
        "submit_time": Optional[str],
        "scheduled_time": Optional[str],
        "end_time": Optional[str],
        "orig_prompt": Optional[str],
        "request_id": Optional[str],
        "usage": Optional[dict],
        "error_code": Optional[str],
        "error_message": Optional[str],
        "raw": dict,        # 原始响应，便于排查
    }
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = _query_url(task_id)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise HappyHorseError(
            f"HappyHorse 查询失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    output = data.get("output") or {}
    usage = data.get("usage")
    return {
        "task_id": output.get("task_id") or task_id,
        "status": output.get("task_status", "UNKNOWN"),
        "video_url": output.get("video_url"),
        "submit_time": output.get("submit_time"),
        "scheduled_time": output.get("scheduled_time"),
        "end_time": output.get("end_time"),
        "orig_prompt": output.get("orig_prompt"),
        "request_id": data.get("request_id"),
        "usage": usage,
        "error_code": output.get("code"),
        "error_message": output.get("message"),
        "raw": data,
    }


async def wait_task(
    task_id: str,
    *,
    poll_interval: float = 15.0,
    max_wait: float = 600.0,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """轮询直到终态或超时。

    Args:
        poll_interval: 轮询间隔秒（DashScope 推荐 ≥15s，QPS 上限 20）
        max_wait:      最长等待秒（默认 10 分钟）
        on_progress:   每次拿到非终态结果后回调，async 或同步均可
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
            result["status"] = "TIMEOUT"
            result["error_code"] = "client.timeout"
            result["error_message"] = f"轮询超时（{max_wait}s）；DashScope 任务 {task_id} 仍未完成"
            return result
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


__all__ = [
    "HappyHorseError",
    "SubmitResult",
    "MODEL_T2V",
    "MODEL_I2V",
    "MODEL_R2V",
    "MODEL_VIDEO_EDIT",
    "SUPPORTED_MODELS",
    "PRICE_PER_SEC",
    "TERMINAL_STATES",
    "estimate_cost",
    "submit_task",
    "query_task",
    "wait_task",
]
