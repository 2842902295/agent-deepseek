"""
Wan3.0（阿里云百炼「万相 3.0」视频生成）异步 API 客户端。

只做协议层翻译：
  - submit_task：POST 创建任务，返回 (task_id, request_id)
  - query_task ：GET 轮询任务，返回标准化的 dict
  - wait_task  ：在客户端循环 query_task 直至终态，便于工具同步使用

协议与 HappyHorse 完全同源（同一套 DashScope 异步任务协议，同端点路径），
凭据与端点来自 load_provider("VIDEO")：VIDEO_WAN / VIDEO_WAN_PRIME 块的
API_KEY / BASE_URL / MODEL（激活块重定向机制，见 config.py）。

模型（All-in-One 全能参考，单模型统一支持 文生视频 / 首尾帧 / 多参考生视频）：
  - wan3.0-video-prime：高速版，端到端速度显著提升
  - wan3.0-video      ：标准版
  模型版本不写死：真相源是块配置的 model 字段（agent_model_block），
  DEFAULT_MODEL 只是块 model 留空时的兜底。

输入约束（官方文档 2026-08）：
  - prompt / media 必填其一；prompt ≤ 20000 字符，参考模式下用
    「图 1 / 视频 1 / 音频 1」按 media 数组内同类型顺序指代素材
  - media.type：first_frame（≤1）/ last_frame（≤1）/ reference_image（≤10）/
    reference_video（≤5 段，总时长 ≤15s）/ reference_audio（≤5 段，总时长 ≤15s）/
    file（≤1）/ link（≤1）；reference_* / file / link 与 first/last_frame 互斥
  - 图片支持 公网 URL / oss:// 临时 URL / data:base64；视频、音频仅公网 URL 或 oss://

parameters：
  - resolution：480P / 720P / 1080P（官方默认 1080P）
  - ratio：adaptive（默认）/ 16:9 / 4:3 / 1:1 / 3:4 / 9:16
  - duration：默认 5；无视频输入 [2, 30]；有视频输入时 输入+输出 ≤ 30 秒；-1 = 智能时长
  - audio：输出是否带原生音频（默认 true，开关声音同价）
  - prompt_extend：prompt 智能改写（默认 true）
  - watermark：水印（默认 false）
  - seed：[0, 2147483647]

价格表来自官方 2026-08 文档（华北 2 北京，元/秒），输入视频秒数与输出视频秒数
同一单价计费。wan3.0-video 标准版官方限时 7 折，表内存原价（折扣随时可能结束）。
失败任务不计费。
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

# ── Endpoints（北京区，与 happyhorse 同端点路径） ────────────────────────────
_DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
# 模型版本真相源是 agent_model_block 中对应块的 model 字段（system-admin 子 agent
# 可改，写后热重载生效），这里只留兜底默认与合法名单。
MODEL_WAN3_VIDEO = "wan3.0-video"
MODEL_WAN3_VIDEO_PRIME = "wan3.0-video-prime"
SUPPORTED_MODELS = (MODEL_WAN3_VIDEO, MODEL_WAN3_VIDEO_PRIME)
DEFAULT_MODEL = MODEL_WAN3_VIDEO_PRIME

SUPPORTED_RESOLUTIONS = ("480P", "720P", "1080P")
SUPPORTED_RATIOS = ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16")
SUPPORTED_MEDIA_TYPES = (
    "first_frame",
    "last_frame",
    "reference_image",
    "reference_video",
    "reference_audio",
    "file",
    "link",
)

# ── 价格（华北 2 北京，元/秒；输入视频 + 输出视频同一单价） ───────────────────
PRICE_PER_SEC: dict[str, dict[str, Decimal]] = {
    MODEL_WAN3_VIDEO_PRIME: {
        "480P": Decimal("0.45"),
        "720P": Decimal("0.90"),
        "1080P": Decimal("1.80"),
    },
    MODEL_WAN3_VIDEO: {
        # 官方原价；限时 7 折不入表（折扣结束后价格即此表值）
        "480P": Decimal("0.30"),
        "720P": Decimal("0.60"),
        "1080P": Decimal("1.20"),
    },
}

# ── 终态集合 ─────────────────────────────────────────────────────────────────
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


class WanVideoError(RuntimeError):
    """Wan3.0 视频生成调用错误。"""


@dataclass
class SubmitResult:
    task_id: str
    request_id: Optional[str]
    raw: dict[str, Any]


def estimate_cost(
    model: Optional[str], resolution: Optional[str], duration_sec: Optional[int]
) -> Optional[Decimal]:
    """按模型 + 分辨率档位 + 秒数估算费用。任一参数缺失或不在表内返回 None。"""
    if not model or not resolution or not duration_sec:
        return None
    table = PRICE_PER_SEC.get(model.strip().lower()) or PRICE_PER_SEC.get(model.strip())
    if table is None:
        return None
    price = table.get(str(resolution).upper().strip())
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
    if not model or model not in SUPPORTED_MODELS:
        raise WanVideoError(
            f"非法的 Wan3.0 模型名：{model}，可选 {' / '.join(SUPPORTED_MODELS)}"
        )
    if not prompt and not media:
        raise WanVideoError("prompt 与 media 必填其一")
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
        # HTTP 只支持异步，缺此头报 "current user api does not support synchronous calls"
        "X-DashScope-Async": "enable",
        # 让 oss:// 临时 URL 在内部解析；不消费 oss URI 时多带无副作用
        OSS_RESOLVE_HEADER: OSS_RESOLVE_VALUE,
    }
    payload = _build_payload(model=model, prompt=prompt, media=media, parameters=parameters)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_submit_url(), headers=headers, json=payload)

    if resp.status_code >= 400:
        raise WanVideoError(
            f"Wan3.0 提交失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    # 提交失败时顶层返回 {code, message, request_id}（如 InvalidApiKey）
    if data.get("code"):
        raise WanVideoError(
            f"Wan3.0 提交失败 code={data.get('code')}：{data.get('message')}"
        )
    output = data.get("output") or {}
    task_id = output.get("task_id")
    if not task_id:
        raise WanVideoError(f"Wan3.0 响应缺少 task_id：{data}")
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
        "video_url": Optional[str],      # SUCCEEDED 时返回（24h 内有效）
        "submit_time": Optional[str],
        "scheduled_time": Optional[str],
        "end_time": Optional[str],
        "orig_prompt": Optional[str],
        "request_id": Optional[str],
        "usage": Optional[dict],         # video_count/duration/input_video_duration/
                                         # output_video_duration/fps/SR/ratio
        "error_code": Optional[str],
        "error_message": Optional[str],
        "raw": dict,                     # 原始响应，便于排查
    }
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = _query_url(task_id)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise WanVideoError(
            f"Wan3.0 查询失败 HTTP {resp.status_code}：{resp.text[:500]}"
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
    max_wait: float = 900.0,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """轮询直到终态或超时。

    Args:
        poll_interval: 轮询间隔秒（DashScope 推荐 ≥15s，QPS 上限 20）
        max_wait:      最长等待秒（官方任务通常 1~5 分钟；30 秒长视频留 15 分钟）
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
    "WanVideoError",
    "SubmitResult",
    "MODEL_WAN3_VIDEO",
    "MODEL_WAN3_VIDEO_PRIME",
    "SUPPORTED_MODELS",
    "DEFAULT_MODEL",
    "SUPPORTED_RESOLUTIONS",
    "SUPPORTED_RATIOS",
    "SUPPORTED_MEDIA_TYPES",
    "PRICE_PER_SEC",
    "TERMINAL_STATES",
    "estimate_cost",
    "submit_task",
    "query_task",
    "wait_task",
]
