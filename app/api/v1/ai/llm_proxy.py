"""
LLM 回环代理：dsh 运行时（pi-ai 适配器）的模型路由指向本服务，
转发到真实 CHAT 块端点时在 wire 上显式注入 thinking 参数。

为什么需要它（08-22 实测）：百炼 Qwen3 系默认开思考；pi-ai 的 openai-completions
路由只有 `enable_thinking = !!reasoningEffort`（qwen 分支还要求 model.reasoning 为真，
而 dsh 插件的 models entry schema 不收 reasoning 字段）——配置路径无法发出
关思考参数，导致每个复杂回合巨慢（数百 reasoning 块）。回环代理复用
`_build_thinking_extra_body` 的按 provider 翻译逻辑，把 thinking 三态落到 wire 上：
百炼 qwen 统一发 reasoning_effort（none=完全关）、kimi 发 reasoning_effort=minimal、
Ollama=think、Claude=thinking.type、自部署=chat_template_kwargs。

两条路由（thinking 来源不同，转发实现共用 _forward）：
- POST /{block_key}/chat/completions              —— env 三态（{BLOCK}_THINKING >
  CHAT_THINKING，空值=不传参交给模型默认）。系统路径 / 无档位块（reasoning_levels
  为空）的 agent 用。
- POST /{block_key}/{level}/chat/completions      —— 用户思考强度（滑块档位，
  见 app/langchain/chat_mode.py）：level 即 wire 档位值（none/low/medium/high/max），
  按块 reasoning_levels 白名单校验（env 物化真相源）后直接注入，跳过 env 逻辑。
  块未配档位或 level 不在白名单 → 404。

安全：仅 loopback 可调（dsh 运行时同机同进程）；凭据由本进程按块注入，dsh 侧不落明文。
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Optional, Union

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from app.langchain.config import _parse_thinking
from app.langchain.llm_providers import _build_thinking_extra_body
from app.langchain.model_selection import block_reasoning_levels

router = APIRouter(prefix="/llm-proxy", tags=["LLM 代理"])

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _check_loopback(request: Request) -> Optional[JSONResponse]:
    """loopback 守卫：非本机调用返回 403 响应；放行返回 None。"""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK:
        return JSONResponse({"code": "4032", "msg": "llm-proxy 仅 loopback 可调"}, status_code=403)
    return None


async def _forward(block_key: str, request: Request, thinking: Optional[Union[bool, str]]):
    """公共转发实现：按块读 env 凭据/端点，注入 thinking 参数后流式转发上游。"""
    base_url = os.environ.get(f"{block_key}_BASE_URL", "")
    api_key = os.environ.get(f"{block_key}_API_KEY", "")
    if not base_url:
        logger.warning(f"[llm-proxy] 块 {block_key} 未配置 BASE_URL，拒绝转发")
        return JSONResponse({"error": f"块 {block_key} 未配置 BASE_URL"}, status_code=502)

    body = await request.json()

    cfg = SimpleNamespace(
        base_url=base_url,
        model=body.get("model") or os.environ.get(f"{block_key}_MODEL", ""),
        api_key=api_key,
    )
    extra = _build_thinking_extra_body(cfg, thinking)
    if extra:
        body.update(extra)
        # 排查用：确认 thinking 参数真的落上了 wire（值不含凭据）
        logger.info(f"[llm-proxy] thinking 注入 block={block_key}: {extra}")

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # 读超时须容得下 max 档思考的长时间静默（上游憋思考不吐字节）；外层还有
    # dsh SDK request_timeout(≤6000s) 与回合看门狗(1800s 分段) 兜底，内宽外紧
    timeout = httpx.Timeout(3600.0, connect=15.0)

    # 测点：把「上游模型延迟」与后端自身开销分开（首包慢排查用）
    t0 = time.monotonic()
    client = httpx.AsyncClient(timeout=timeout)
    try:
        req = client.build_request("POST", url, json=body, headers=headers)
        up = await client.send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        logger.warning(f"[llm-proxy] 上游连接失败 block={block_key}: {e}")
        return JSONResponse({"error": f"上游连接失败：{e}"}, status_code=502)
    logger.info(f"[perf] llm-proxy 上游响应头 block={block_key} model={cfg.model}: {(time.monotonic() - t0) * 1000:.0f}ms")

    # 上游非 2xx 会原样透传状态码（dsh pi-ai 侧按 SERVER 错误重试）；留痕便于排查
    # 「502 (no body)」这类静默故障——曾因本地自部署上游空体 502 无从定位
    if up.status_code >= 400:
        logger.warning(f"[llm-proxy] 上游返回错误状态 block={block_key} model={cfg.model} status={up.status_code}")

    media = up.headers.get("content-type", "text/event-stream")

    async def _gen():
        first = True
        try:
            async for chunk in up.aiter_raw():
                if first:
                    first = False
                    logger.info(f"[perf] llm-proxy 上游首包 block={block_key}: {(time.monotonic() - t0) * 1000:.0f}ms")
                yield chunk
        finally:
            await up.aclose()
            await client.aclose()

    return StreamingResponse(_gen(), status_code=up.status_code, media_type=media)


@router.post("/{block_key}/chat/completions")
async def proxy_chat(block_key: str, request: Request):
    """env 三态路由：{BLOCK}_THINKING > CHAT_THINKING（见 config._parse_thinking）。

    空值/未设置 → None → 不发送任何 thinking 参数（交给模型默认）。
    """
    if (denied := _check_loopback(request)) is not None:
        return denied
    thinking = _parse_thinking(os.environ.get(f"{block_key}_THINKING") or os.environ.get("CHAT_THINKING"))
    return await _forward(block_key, request, thinking)


@router.post("/{block_key}/{level}/chat/completions")
async def proxy_chat_with_level(block_key: str, level: str, request: Request):
    """用户思考强度路由（滑块档位）：level 即 wire 档位值（none/low/medium/high/max），
    按块 reasoning_levels 白名单校验后直接注入，跳过 env 逻辑。
    块未配档位或 level 非法 → 404（防路径段被当探测把手）。
    """
    if (denied := _check_loopback(request)) is not None:
        return denied
    levels = block_reasoning_levels(block_key)
    if not levels or level not in levels:
        return JSONResponse({"error": f"块 {block_key} 不支持思考强度档位：{level}"}, status_code=404)
    return await _forward(block_key, request, level)


__all__ = ["router"]
