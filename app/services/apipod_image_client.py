"""
APIPod（GPT-image-2）图像生成异步 API 客户端。

只做协议层翻译：
  - submit_task：POST /images/generations 创建任务，返回 (task_id, raw)
  - query_task ：GET  /images/status/{task_id} 轮询任务，返回标准化 dict
  - wait_task  ：在客户端循环 query_task 直至终态，便于工具同步使用

凭据与端点来自 load_provider("IMAGE")：IMAGE_PROVIDER=apipod 块的
API_KEY / MODEL / BASE_URL（激活块重定向机制与视频侧一致）。

与 QuickRouter 同步版 gpt_image_client 的关键差异：
  - 响应是统一信封 {code, message, data}：HTTP 200 也可能是业务失败（code != 200，
    402 = 余额不足 / 429 = 限流 / 401 = 凭据无效），必须同时检查 HTTP 状态与 body code
  - 全异步任务式：提交只返回 task_id，图像要轮询 /images/status/{task_id} 拿 result[]
  - 参数面不同：没有 n/size/format，改用 aspect_ratio（1:1 / 16:9 等）+
    resolution（1K/2K/4K）+ quality（auto/low/medium/high）
  - 图像编辑/参考图走 image_urls（公网 URL，最多 16 张），不支持 base64/multipart
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
MODEL_GPT_IMAGE_2 = "gpt-image-2"
# IMAGE_MODEL 留空时的默认
DEFAULT_MODEL = MODEL_GPT_IMAGE_2

SUPPORTED_ASPECT_RATIOS = (
    "auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
)
SUPPORTED_RESOLUTIONS = ("1K", "2K", "4K")
SUPPORTED_QUALITIES = ("auto", "low", "medium", "high")
MAX_IMAGE_URLS = 16
MAX_PROMPT_LEN = 32000

# ── 终态集合 ─────────────────────────────────────────────────────────────────
TERMINAL_STATES = {"completed", "failed", "cancelled"}

# ── 瞬时网络错误重试 ─────────────────────────────────────────────────────────
# 部分网络环境（IPv6 路由不通、系统代理抖动等）会偶发 ConnectError/TLS 握手中断，
# 重试 3 次可显著提高成功率（curl 稳定正是因为会自动回退重试）
_RETRY_COUNT = 3


class ApipodImageError(RuntimeError):
    """APIPod 图像生成调用错误。"""


@dataclass
class ApipodSubmitResult:
    task_id: str
    raw: dict[str, Any]


def _api_base() -> str:
    return (load_provider("IMAGE").base_url or _DEFAULT_BASE).rstrip("/")


def _api_key() -> str:
    return load_provider("IMAGE").api_key


def _model() -> str:
    return load_provider("IMAGE").model or DEFAULT_MODEL


async def _http_with_retry(factory: Any, *, action: str) -> httpx.Response:
    """执行一次 HTTP 请求；瞬时网络错误（连接被掐断/TLS 握手失败等）自动重试。

    业务错误（HTTP >= 400、code != 200）不在此处理，由调用方解析响应。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, _RETRY_COUNT + 1):
        try:
            return await factory()
        except httpx.TransportError as e:
            last_exc = e
            if attempt < _RETRY_COUNT:
                delay = 1.0 * attempt
                logger.warning(
                    f"APIPod {action}网络瞬时错误（第 {attempt}/{_RETRY_COUNT} 次），"
                    f"{delay}s 后重试：{type(e).__name__} {e}"
                )
                await asyncio.sleep(delay)
    raise ApipodImageError(
        f"APIPod {action}网络错误（重试 {_RETRY_COUNT} 次仍失败）："
        f"{type(last_exc).__name__} {last_exc}"
    )


async def submit_task(
    *,
    prompt: str,
    image_urls: Optional[list[str]] = None,
    aspect_ratio: str = "auto",
    resolution: str = "1K",
    quality: str = "auto",
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> ApipodSubmitResult:
    """创建图像生成任务，返回 task_id。

    Args:
        prompt:       提示词（≤32000 字），必填；图像编辑时是编辑指令
        image_urls:   参考图公网 URL 列表，最多 16 张（可选，传了即图像编辑/多图融合）
        aspect_ratio: auto（默认）/ 1:1 / 2:3 / 3:2 / 3:4 / 4:3 / 4:5 / 5:4 / 9:16 / 16:9 / 21:9
        resolution:   1K（默认）/ 2K / 4K
        quality:      auto（默认）/ low / medium / high
        model:        模型 id，留空走 IMAGE_MODEL / 内置默认 gpt-image-2
    """
    if not prompt or not prompt.strip():
        raise ApipodImageError("prompt 不能为空")
    selected_model = model or _model()
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ApipodImageError(
            f"不支持的 aspect_ratio：{aspect_ratio}，可选 {SUPPORTED_ASPECT_RATIOS}"
        )
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ApipodImageError(
            f"不支持的 resolution：{resolution}，可选 {SUPPORTED_RESOLUTIONS}"
        )
    if quality not in SUPPORTED_QUALITIES:
        raise ApipodImageError(
            f"不支持的 quality：{quality}，可选 {SUPPORTED_QUALITIES}"
        )
    if image_urls and len(image_urls) > MAX_IMAGE_URLS:
        raise ApipodImageError(f"参考图最多 {MAX_IMAGE_URLS} 张，收到 {len(image_urls)} 张")

    payload: dict[str, Any] = {
        "model": selected_model,
        "prompt": prompt[:MAX_PROMPT_LEN],
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "quality": quality,
    }
    if image_urls:
        payload["image_urls"] = list(image_urls)

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    # trust_env=False：不读取系统代理。本机 Clash 等系统代理（127.0.0.1:7890）会把到
    # api.apipod.ai 的 CONNECT+TLS 隧道掐断（EndOfStream，100% 失败），而该服务可直连；
    # 生产 Docker 无代理，行为不变
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await _http_with_retry(
            lambda: client.post(f"{_api_base()}/images/generations", headers=headers, json=payload),
            action="图像任务提交",
        )

    if resp.status_code >= 400:
        raise ApipodImageError(
            f"APIPod 图像任务提交失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    # 统一信封：{code, message, data:{task_id}}；HTTP 200 也可能是业务失败
    if data.get("code") != 200:
        raise ApipodImageError(
            f"APIPod 提交失败 code={data.get('code')}：{data.get('message') or resp.text[:300]}"
        )
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise ApipodImageError(f"APIPod 响应缺少 data.task_id：{data}")
    return ApipodSubmitResult(task_id=task_id, raw=data)


async def query_task(task_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """查询任务状态，返回结构化字典：

    {
        "task_id": str,
        "status": "pending/processing/completed/failed/cancelled/unknown",
        "images": list[str],             # 生成的图像 URL 数组（result 字段）
        "completed_at": Optional[int],
        "error_message": Optional[str],
        "raw": dict,                     # 原始响应，便于排查
    }
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = f"{_api_base()}/images/status/{task_id}"
    # trust_env=False：同 submit_task，绕开掐断 TLS 的系统代理
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await _http_with_retry(
            lambda: client.get(url, headers=headers),
            action="图像任务查询",
        )
    if resp.status_code >= 400:
        raise ApipodImageError(
            f"APIPod 图像任务查询失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    if data.get("code") != 200:
        raise ApipodImageError(
            f"APIPod 查询失败 code={data.get('code')}：{data.get('message')}"
        )
    d = data.get("data") or {}
    result = d.get("result")
    images: list[str] = []
    if isinstance(result, list):
        images = [u for u in result if isinstance(u, str) and u]
    status = (d.get("status") or "unknown").lower()
    if status == "completed" and not images:
        logger.warning(
            f"APIPod 图像任务 {task_id} 已完成但 result 为空；raw={data}"
        )
    return {
        "task_id": d.get("task_id") or task_id,
        "status": status,
        "images": images,
        "completed_at": d.get("completed_at"),
        "error_message": d.get("error_message") or d.get("error"),
        "raw": data,
    }


def _poll_delay(elapsed: float) -> float:
    """与视频侧一致：前 30s 每 3s；30s~2min 每 8s；之后每 20s。"""
    if elapsed < 30:
        return 3.0
    if elapsed < 120:
        return 8.0
    return 20.0


async def wait_task(
    task_id: str,
    *,
    max_wait: float = 600.0,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """轮询直到终态或超时（间隔按 _poll_delay 自适应）。

    Args:
        max_wait:    最长等待秒（默认 10 分钟）
        on_progress: 每次拿到非终态结果后回调，async 或同步均可
    """
    elapsed = 0.0
    while True:
        try:
            result = await query_task(task_id)
        except httpx.TransportError as e:
            # 瞬时网络抖动（代理/TLS 握手失败等）：按轮询节奏重试，不立即整体失败；
            # 业务错误（401 凭据无效 / 404 任务不存在等）仍照常向上抛出
            logger.warning(f"APIPod 图像任务 {task_id} 查询瞬时错误，稍后重试：{e}")
            if elapsed >= max_wait:
                return {
                    "task_id": task_id,
                    "status": "timeout",
                    "images": [],
                    "completed_at": None,
                    "error_message": f"轮询超时（{max_wait}s）；最近一次查询错误：{e}",
                    "raw": {},
                }
            delay = _poll_delay(elapsed)
            await asyncio.sleep(delay)
            elapsed += delay
            continue
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
            result["error_message"] = f"轮询超时（{max_wait}s）；APIPod 图像任务 {task_id} 仍未完成"
            return result
        delay = _poll_delay(elapsed)
        await asyncio.sleep(delay)
        elapsed += delay


__all__ = [
    "ApipodImageError",
    "ApipodSubmitResult",
    "MODEL_GPT_IMAGE_2",
    "DEFAULT_MODEL",
    "SUPPORTED_ASPECT_RATIOS",
    "SUPPORTED_RESOLUTIONS",
    "SUPPORTED_QUALITIES",
    "MAX_IMAGE_URLS",
    "MAX_PROMPT_LEN",
    "TERMINAL_STATES",
    "submit_task",
    "query_task",
    "wait_task",
]
