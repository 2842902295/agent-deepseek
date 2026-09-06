"""
火山方舟（Volcengine Ark）Seedance 视频生成异步 API 客户端。

只做协议层翻译：
  - submit_task：POST /contents/generations/tasks 创建任务，返回 (task_id, raw)
  - query_task ：GET  /contents/generations/tasks/{id} 轮询任务，返回标准化 dict
  - wait_task  ：在客户端循环 query_task 直至终态，便于工具同步使用

凭据与端点来自 load_provider("VIDEO")：VIDEO_PROVIDER / VIDEO_API_KEY / VIDEO_BASE_URL；
地域默认北京（与文档默认一致）。

与阿里 HappyHorse 的关键差异：
  - 鉴权头是普通 Bearer，不需要 X-DashScope-Async / OssResourceResolve
  - 输入字段是 content（数组），元素以 type 区分文本/图/视频/音频，可带 role
  - 终态字符串是小写：succeeded / failed / cancelled / queued / processing
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from loguru import logger

from app.langchain.config import load_provider

# ── Endpoints ────────────────────────────────────────────────────────────────
_ARK_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_OR_DEFAULT_BASE = "https://openrouter.ai/api/v1"

# ── 模型常量 ─────────────────────────────────────────────────────────────────
# fast 是当前默认（成本最低，省钱优先）；pro 在 fast 不达预期时再升级
MODEL_SEEDANCE_2_0_FAST = "doubao-seedance-2-0-fast-260128"
MODEL_SEEDANCE_2_0_PRO = "doubao-seedance-2-0-260128"
MODEL_SEEDANCE_PRO = "doubao-seedance-pro"
MODEL_OR_SEEDANCE_2_0_FAST = "bytedance/seedance-2.0-fast"
# VIDEO_MODEL 留空时的默认（Ark 走 fast；OpenRouter 在工具层选 bytedance/seedance-2.0-fast）
DEFAULT_MODEL = MODEL_SEEDANCE_2_0_FAST

# ── 终态集合 ─────────────────────────────────────────────────────────────────
# Ark: succeeded/failed/cancelled/canceled
# OpenRouter: completed/failed/cancelled/expired
TERMINAL_STATES = {"succeeded", "completed", "failed", "cancelled", "canceled", "expired"}


class ArkVideoError(RuntimeError):
    """Ark 视频生成调用错误。"""


@dataclass
class ArkSubmitResult:
    task_id: str
    status: str
    raw: dict[str, Any]


def _use_openrouter() -> bool:
    return load_provider("VIDEO").provider == "openrouter"


def _api_key() -> str:
    return load_provider("VIDEO").api_key


def _api_base() -> str:
    cfg = load_provider("VIDEO")
    if cfg.base_url:
        return cfg.base_url.rstrip("/")
    return _OR_DEFAULT_BASE if cfg.provider == "openrouter" else _ARK_DEFAULT_BASE


def _submit_url() -> str:
    base = _api_base()
    return f"{base}/videos" if _use_openrouter() else f"{base}/contents/generations/tasks"


def _query_url(task_id: str) -> str:
    base = _api_base()
    tpl = f"{base}/videos/{{task_id}}" if _use_openrouter() else f"{base}/contents/generations/tasks/{{task_id}}"
    return tpl.format(task_id=task_id)


def _build_payload(
    *,
    model: str,
    content: list[dict[str, Any]],
    generate_audio: Optional[bool] = None,
    ratio: Optional[str] = None,
    duration: Optional[int] = None,
    resolution: Optional[str] = None,
    fps: Optional[int] = None,
    seed: Optional[int] = None,
    watermark: Optional[bool] = None,
    camera_fixed: Optional[bool] = None,
) -> dict[str, Any]:
    if not model:
        raise ArkVideoError("model 必填")

    if _use_openrouter():
        # OpenRouter: prompt + 可选 frame_images/input_references
        if not content:
            raise ArkVideoError("content 至少包含一项（文本提示词）")

        # 提取 text 作为 prompt
        prompt = ""
        frame_images = []
        input_references = []

        for item in content:
            if item.get("type") == "text":
                prompt = item.get("text", "")
            elif item.get("type") == "image_url":
                img_obj = {
                    "type": "image_url",
                    "image_url": item["image_url"]
                }
                role = item.get("role", "")
                if role in ("first_frame", "last_frame"):
                    img_obj["frame_type"] = role
                    frame_images.append(img_obj)
                else:
                    input_references.append(img_obj)

        if not prompt:
            raise ArkVideoError("OpenRouter 必须提供文本提示词")

        payload: dict[str, Any] = {"model": model, "prompt": prompt}

        if frame_images:
            payload["frame_images"] = frame_images
        if input_references:
            payload["input_references"] = input_references

        # OpenRouter 参数名映射
        if ratio is not None:
            payload["aspect_ratio"] = ratio
        if duration is not None:
            payload["duration"] = duration
        if resolution is not None:
            payload["resolution"] = resolution
        if seed is not None:
            payload["seed"] = seed
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio

        return payload

    # Ark 原有逻辑
    if not content:
        raise ArkVideoError("content 至少包含一项（一般是 type=text 的提示词）")
    payload: dict[str, Any] = {"model": model, "content": content}
    for k, v in (
        ("generate_audio", generate_audio),
        ("ratio", ratio),
        ("duration", duration),
        ("resolution", resolution),
        ("fps", fps),
        ("seed", seed),
        ("watermark", watermark),
        ("camera_fixed", camera_fixed),
    ):
        if v is not None:
            payload[k] = v
    return payload


async def submit_task(
    *,
    model: str,
    content: list[dict[str, Any]],
    generate_audio: Optional[bool] = None,
    ratio: Optional[str] = None,
    duration: Optional[int] = None,
    resolution: Optional[str] = None,
    fps: Optional[int] = None,
    seed: Optional[int] = None,
    watermark: Optional[bool] = None,
    camera_fixed: Optional[bool] = None,
    timeout: float = 60.0,
) -> ArkSubmitResult:
    """创建视频生成任务，返回 task_id。"""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    payload = _build_payload(
        model=model,
        content=content,
        generate_audio=generate_audio,
        ratio=ratio,
        duration=duration,
        resolution=resolution,
        fps=fps,
        seed=seed,
        watermark=watermark,
        camera_fixed=camera_fixed,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_submit_url(), headers=headers, json=payload)

    if resp.status_code >= 400:
        raise ArkVideoError(
            f"Ark 视频任务提交失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise ArkVideoError(f"Ark 响应缺少 id 字段：{data}")
    return ArkSubmitResult(
        task_id=task_id,
        status=data.get("status", "queued"),
        raw=data,
    )


def _extract_video_url(data: dict[str, Any]) -> Optional[str]:
    """从查询响应里挖 video_url。"""
    # OpenRouter: unsigned_urls[0]
    unsigned_urls = data.get("unsigned_urls")
    if isinstance(unsigned_urls, list) and unsigned_urls:
        u = unsigned_urls[0]
        if isinstance(u, str) and u:
            return u

    # ① 顶层（Ark 文档主路径）
    url = data.get("video_url")
    if isinstance(url, str) and url:
        return url

    # ② content[*].video_url（Ark chat-like 结构）
    content = data.get("content")
    candidates: list[Any] = []
    if isinstance(content, dict):
        candidates.append(content)
    elif isinstance(content, list):
        candidates.extend(content)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        v = item.get("video_url")
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            inner = v.get("url")
            if isinstance(inner, str) and inner:
                return inner
        if isinstance(item.get("url"), str) and item["url"]:
            return item["url"]

    # ③ data.data.video_url 等包装
    for wrapper_key in ("data", "result", "output"):
        wrapped = data.get(wrapper_key)
        if isinstance(wrapped, dict):
            v = wrapped.get("video_url")
            if isinstance(v, str) and v:
                return v
    return None


async def query_task(task_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """查询任务状态，返回结构化字典。"""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = _query_url(task_id)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise ArkVideoError(
            f"视频任务查询失败 HTTP {resp.status_code}：{resp.text[:500]}"
        )
    data = resp.json()
    err = data.get("error") or {}
    raw_status = (data.get("status") or "unknown").lower()

    # 统一为 ark 风格：OpenRouter 的 completed → succeeded
    status = "succeeded" if raw_status == "completed" else raw_status

    video_url = _extract_video_url(data)
    if status == "succeeded" and not video_url:
        logger.warning(
            f"视频任务 {task_id} 已完成但解析不到 video_url；raw keys={list(data.keys())}；raw={data}"
        )
    return {
        "task_id": data.get("id") or task_id,
        "model": data.get("model"),
        "status": status,
        "video_url": video_url,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "usage": data.get("usage"),
        "duration": data.get("duration"),
        "resolution": data.get("resolution"),
        "ratio": data.get("ratio") or data.get("aspect_ratio"),
        "fps": data.get("framespersecond") or data.get("fps"),
        "seed": data.get("seed"),
        "error_code": err.get("code") if isinstance(err, dict) else None,
        "error_message": err.get("message") if isinstance(err, dict) else (str(err) if err else None),
        "raw": data,
    }


async def wait_task(
    task_id: str,
    *,
    poll_interval: float = 10.0,
    max_wait: float = 600.0,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """轮询直到终态或超时。

    Args:
        poll_interval: 轮询间隔秒（文档建议 ≥10s）
        max_wait:      最长等待秒（默认 10 分钟，1080P 长视频可适当调大）
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
            result["status"] = "timeout"
            result["error_code"] = "client.timeout"
            result["error_message"] = f"轮询超时（{max_wait}s）；Ark 任务 {task_id} 仍未完成"
            return result
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


__all__ = [
    "ArkVideoError",
    "ArkSubmitResult",
    "MODEL_SEEDANCE_2_0_FAST",
    "MODEL_SEEDANCE_2_0_PRO",
    "MODEL_SEEDANCE_PRO",
    "DEFAULT_MODEL",
    "TERMINAL_STATES",
    "submit_task",
    "query_task",
    "wait_task",
]
