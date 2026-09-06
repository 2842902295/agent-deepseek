"""
MinerU 文档解析工具

调用 MinerU Gradio API 将文档（PDF、图片、Word、PPT、Excel 等）转换为 Markdown。
流程：先通过 /gradio_api/upload 上传文件，再调用转换接口（两段式 SSE）。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

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


async def _upload_file(client: httpx.AsyncClient, file_url: str) -> str:
    """从 file_url 下载文件后上传到 MinerU，返回 Gradio 服务器路径。"""
    # 下载原始文件
    logger.info(f"MinerU 下载文件：{file_url}")
    dl = await client.get(file_url, timeout=60, follow_redirects=True)
    dl.raise_for_status()

    if not dl.content:
        raise MinerUError(f"图片下载失败：响应内容为空，URL={file_url}")

    filename = file_url.rstrip("/").split("/")[-1].split("?")[0] or "file"
    content_type = dl.headers.get("content-type", "application/octet-stream").split(";")[0].strip()

    # 上传到 MinerU Gradio
    resp = await client.post(
        f"{settings.MINERU_BASE_URL}{_UPLOAD_PATH}",
        files={"files": (filename, dl.content, content_type)},
        timeout=60,
    )
    resp.raise_for_status()
    paths = resp.json()  # 返回 ["/tmp/gradio/xxx/filename"]
    if not paths or not isinstance(paths, list):
        raise MinerUError(f"MinerU 上传失败，响应：{paths}")
    server_path = paths[0]
    logger.info(f"MinerU 上传完成，服务器路径：{server_path}")
    return server_path


async def _post_task(client: httpx.AsyncClient, server_path: str, **kwargs) -> str:
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
    return event_id


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
    async with httpx.AsyncClient() as client:
        server_path = await _upload_file(client, file_url)

        event_id = await _post_task(
            client, server_path,
            end_pages=end_pages,
            is_ocr=is_ocr,
            formula_enable=formula_enable,
            table_enable=table_enable,
            image_analysis=image_analysis,
            effort=effort,
            language=language,
            backend=backend,
            url=url,
        )
        logger.info(f"MinerU 任务已提交，event_id={event_id}")

        markdown = ""
        event_count = 0
        async for event in _stream_result(client, event_id):
            event_count += 1
            if event is None:
                continue
            # Gradio SSE：list 事件为进度更新，最后一条 data[2] 是 Markdown 输出
            if isinstance(event, list) and len(event) > 2:
                if event[2]:
                    markdown = event[2]
            elif isinstance(event, dict):
                if event.get("msg") == "process_completed":
                    output = event.get("output", {})
                    data = output.get("data", [])
                    if len(data) > 2 and data[2]:
                        markdown = data[2]
                    elif data and data[0]:
                        markdown = data[0]
                    else:
                        logger.warning(f"[MinerU SSE] process_completed 但 data 为空: {data}")
                elif event.get("msg") == "process_errored":
                    raise MinerUError(f"MinerU 转换失败：{event}")

        if not markdown:
            raise MinerUError("MinerU 返回空结果")

        logger.info(f"MinerU 转换完成，markdown 内容：{markdown}")
        return markdown
