"""
MinerU 文档解析工具

调用 MinerU Gradio API 将文档（PDF、图片、Word、PPT、Excel 等）转换为 Markdown。
流程：先通过 /gradio_api/upload 上传文件，再调用转换接口（两段式 SSE）。

两个入口：
- `convert_bytes_to_markdown(data, filename, ...)`：**已有文件字节**时用（定时回填走这条——
  下载与解析分离，下载失败可换候选地址、解析失败可换 backend，互不混淆）
- `convert_to_markdown(file_url, ...)`：只有 URL 时用（内部先下载再走上面那条）
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from loguru import logger

from app.settings import APP_SETTINGS as settings

_UPLOAD_PATH = "/gradio_api/upload"
_API_PATH = "/gradio_api/call/convert_to_markdown_stream"

# 默认参数（与 Gradio API 文档一致）
_DEFAULT_END_PAGES = 10000
_DEFAULT_IS_OCR = True
_DEFAULT_FORMULA_ENABLE = True
_DEFAULT_TABLE_ENABLE = True
_DEFAULT_IMAGE_ANALYSIS = True
_DEFAULT_EFFORT = "medium"
_DEFAULT_LANGUAGE = "ch (Chinese, English, Japanese, Chinese Traditional, Latin)"
_DEFAULT_BACKEND = "vlm-engine"  # 合法值: pipeline | vlm-engine | hybrid-engine
_DEFAULT_URL = "http://localhost:30000"


class MinerUError(Exception):
    pass


async def _upload_bytes(client: httpx.AsyncClient, data: bytes, filename: str, content_type: str) -> str:
    """把文件字节上传到 MinerU Gradio，返回服务器路径。"""
    resp = await client.post(
        f"{settings.MINERU_BASE_URL}{_UPLOAD_PATH}",
        files={"files": (filename, data, content_type)},
        timeout=60,
    )
    resp.raise_for_status()
    paths = resp.json()  # 返回 ["/tmp/gradio/xxx/filename"]
    if not paths or not isinstance(paths, list):
        raise MinerUError(f"MinerU 上传失败，响应：{paths}")
    server_path = str(paths[0])
    logger.debug(f"MinerU 上传完成，服务器路径：{server_path}")
    return server_path


async def _upload_file(client: httpx.AsyncClient, file_url: str) -> str:
    """从 file_url 下载文件后上传到 MinerU，返回 Gradio 服务器路径。"""
    logger.debug(f"MinerU 下载文件：{file_url}")
    dl = await client.get(file_url, timeout=60, follow_redirects=True)
    dl.raise_for_status()

    if not dl.content:
        raise MinerUError(f"图片下载失败：响应内容为空，URL={file_url}")

    filename = file_url.rstrip("/").split("/")[-1].split("?")[0] or "file"
    content_type = dl.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    return await _upload_bytes(client, dl.content, filename, content_type)


async def _post_task(client: httpx.AsyncClient, server_path: str, **kwargs: Any) -> str:
    """提交转换任务，返回 event_id。"""
    payload = {
        "data": [
            {"path": server_path, "meta": {"_type": "gradio.FileData"}},
            kwargs.get("end_pages", _DEFAULT_END_PAGES),
            kwargs.get("is_ocr", _DEFAULT_IS_OCR),
            kwargs.get("formula_enable", _DEFAULT_FORMULA_ENABLE),
            kwargs.get("table_enable", _DEFAULT_TABLE_ENABLE),
            kwargs.get("image_analysis", _DEFAULT_IMAGE_ANALYSIS),
            kwargs.get("effort", _DEFAULT_EFFORT),
            kwargs.get("language", _DEFAULT_LANGUAGE),
            kwargs.get("backend", _DEFAULT_BACKEND),
            kwargs.get("url", _DEFAULT_URL),
        ]
    }
    resp = await client.post(
        f"{settings.MINERU_BASE_URL}{_API_PATH}",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    event_id = data.get("event_id")
    if not event_id:
        raise MinerUError(f"未获取到 event_id，响应：{data}")
    return str(event_id)


async def _stream_result(client: httpx.AsyncClient, event_id: str) -> AsyncIterator[dict]:
    """SSE 流式读取转换结果，逐事件 yield。"""
    url = f"{settings.MINERU_BASE_URL}{_API_PATH}/{event_id}"
    async with client.stream("GET", url, timeout=600) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                yield {"raw": raw}


async def _parse_server_path(client: httpx.AsyncClient, server_path: str, opts: dict[str, Any]) -> str:
    """提交已上传的文件并等待解析完成，返回 Markdown。"""
    event_id = await _post_task(client, server_path, **opts)
    logger.debug(f"MinerU 任务已提交，event_id={event_id} backend={opts.get('backend', _DEFAULT_BACKEND)}")

    markdown = ""
    async for event in _stream_result(client, event_id):
        if event is None:
            continue
        # Gradio SSE：list 事件为进度更新，最后一条 data[2] 是 Markdown 输出
        if isinstance(event, list) and len(event) > 2:
            if event[2]:
                markdown = str(event[2])
        elif isinstance(event, dict):
            if event.get("msg") == "process_completed":
                output = event.get("output", {})
                data = output.get("data", [])
                if len(data) > 2 and data[2]:
                    markdown = str(data[2])
                elif data and data[0]:
                    markdown = str(data[0])
                else:
                    logger.warning(f"[MinerU SSE] process_completed 但 data 为空: {data}")
            elif event.get("msg") == "process_errored":
                raise MinerUError(f"MinerU 转换失败：{event}")

    if not markdown:
        raise MinerUError("MinerU 返回空结果")

    # 批量回填一轮数万条，整段 Markdown 与"完成"日志都只在 DEBUG 打，
    # 进度由调用方（scheduler 每 N 条汇总一次）负责，避免 INFO 刷屏
    logger.debug(f"MinerU 转换完成：{len(markdown)} 字符，内容：{markdown}")
    return markdown


def _opts(
    end_pages: int,
    is_ocr: bool,
    formula_enable: bool,
    table_enable: bool,
    image_analysis: bool,
    effort: str,
    language: str,
    backend: str,
    url: str,
) -> dict[str, Any]:
    return {
        "end_pages": end_pages,
        "is_ocr": is_ocr,
        "formula_enable": formula_enable,
        "table_enable": table_enable,
        "image_analysis": image_analysis,
        "effort": effort,
        "language": language,
        "backend": backend,
        "url": url,
    }


async def convert_bytes_to_markdown(
    data: bytes,
    filename: str,
    *,
    content_type: str = "application/octet-stream",
    client: httpx.AsyncClient | None = None,
    end_pages: int = _DEFAULT_END_PAGES,
    is_ocr: bool = _DEFAULT_IS_OCR,
    formula_enable: bool = _DEFAULT_FORMULA_ENABLE,
    table_enable: bool = _DEFAULT_TABLE_ENABLE,
    image_analysis: bool = _DEFAULT_IMAGE_ANALYSIS,
    effort: str = _DEFAULT_EFFORT,
    language: str = _DEFAULT_LANGUAGE,
    backend: str = _DEFAULT_BACKEND,
    url: str = _DEFAULT_URL,
) -> str:
    """
    把**已在手的文件字节**转换为 Markdown 文本。

    data/filename/content_type: 文件内容与上传用的文件名（扩展名影响 MinerU 的解析分支）。
    client: 复用的 httpx 客户端（批量任务传入以复用连接）；不传则内部临时建一个。
    转换失败时抛出 MinerUError（或 httpx 异常，如 MinerU 服务 5xx）。
    """
    opts = _opts(end_pages, is_ocr, formula_enable, table_enable, image_analysis, effort, language, backend, url)
    if client is not None:
        server_path = await _upload_bytes(client, data, filename, content_type)
        return await _parse_server_path(client, server_path, opts)

    async with httpx.AsyncClient() as own_client:
        server_path = await _upload_bytes(own_client, data, filename, content_type)
        return await _parse_server_path(own_client, server_path, opts)


async def convert_to_markdown(
    file_url: str,
    *,
    end_pages: int = _DEFAULT_END_PAGES,
    is_ocr: bool = _DEFAULT_IS_OCR,
    formula_enable: bool = _DEFAULT_FORMULA_ENABLE,
    table_enable: bool = _DEFAULT_TABLE_ENABLE,
    image_analysis: bool = _DEFAULT_IMAGE_ANALYSIS,
    effort: str = _DEFAULT_EFFORT,
    language: str = _DEFAULT_LANGUAGE,
    backend: str = _DEFAULT_BACKEND,
    url: str = _DEFAULT_URL,
) -> str:
    """
    将文档（PDF、图片、Word、PPT、Excel 等）转换为 Markdown 文本。

    file_url: 文件的可访问 HTTP URL，先下载再上传到 MinerU。
    返回 Markdown 字符串，转换失败时抛出 MinerUError。
    """
    opts = _opts(end_pages, is_ocr, formula_enable, table_enable, image_analysis, effort, language, backend, url)
    async with httpx.AsyncClient() as client:
        server_path = await _upload_file(client, file_url)
        return await _parse_server_path(client, server_path, opts)
