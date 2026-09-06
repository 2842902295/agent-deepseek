"""
MinerU 单条测试接口

用于单独调试定时任务（fill_image_text）中图片识别失败的记录：走与定时任务**完全相同**的
候选地址解析（`app/utils/image_ref.py`）与 backend 回退逻辑，把每一步尝试明细返回，
不写入数据库、不写入日志表（因此也能用来手工重试已被熔断 status='dead' 的记录）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.models.standard.jgh_pdf import StandardJghPdfFormula, StandardJghPdfTable
from app.schemas.base import Fail, Success
from app.settings.config import settings
from app.utils.image_ref import ImageFetchError, build_image_candidates, fetch_first_available
from app.utils.mineru import MinerUError, convert_bytes_to_markdown

router = APIRouter(prefix="/mineru-test", tags=["MinerU测试"])

# 与 scheduler.py 的 fill_image_text 保持一致
_BACKENDS = ("vlm-engine", "pipeline")
_MAX_RETRIES = 5
_RETRY_DELAY = 5
_DOWNLOAD_TIMEOUT = 60.0


class MinerUTestRequest(BaseModel):
    type: Literal["table", "foluma"] = Field(..., description="类型：table（表格）或 foluma（公式）")
    id: int = Field(..., description="standard_jgh_pdf_table 或 standard_jgh_pdf_formula 的主键 ID")


@router.post("")
async def test_mineru_recognition(req: MinerUTestRequest):
    """
    单条测试 MinerU 图片识别（模拟定时任务逻辑，含候选地址轮转 + backend 降级 + 重试）。

    - type: table → 查 standard_jgh_pdf_table；foluma → 查 standard_jgh_pdf_formula
    - id: 对应表的主键
    - 结果不写入数据库和日志表，仅返回（含每个候选地址 / 每次解析尝试的明细）
    """
    model_cls = StandardJghPdfTable if req.type == "table" else StandardJghPdfFormula
    table_name = str(model_cls.Meta.table)

    record = await model_cls.filter(id=req.id).first()
    if not record:
        return Fail(code="4004", msg=f"未找到记录：{table_name} id={req.id}")

    file_name: str | None = record.file_name
    image_field: str | None = record.image  # 可能是 <img> 标签、也可能是 /oss/... 相对路径

    if not file_name and not image_field:
        return Fail(code="4000", msg=f"该记录 file_name 和 image 均为空：{table_name} id={req.id}")

    candidates = build_image_candidates(
        file_name=file_name,
        image=image_field,
        base_url=settings.JGH_IMAGE_BASE_URL,
        public_base_url=settings.JGH_IMAGE_PUBLIC_BASE_URL,
        legacy_host=settings.JGH_IMAGE_LEGACY_HOST,
    )
    base_data: dict[str, Any] = {
        "type": req.type,
        "id": req.id,
        "table": table_name,
        "file_name": file_name,
        "image": (image_field or "")[:500],
        "candidates": candidates,
    }
    if not candidates:
        return Fail(code="4000", msg="file_name / image 解析不出可用图片地址（可能是占位图或脏值）", data=base_data)

    logger.info(f"[MinerUTest] 开始测试 type={req.type} id={req.id} 候选 {len(candidates)} 个，首选 {candidates[0]}")

    t0 = time.monotonic()
    download_attempts: list[dict[str, Any]] = []
    parse_attempts: list[dict[str, Any]] = []
    markdown = ""
    last_error = ""
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)

    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=_DOWNLOAD_TIMEOUT), limits=limits) as client:
        # ① 下载：候选地址逐个尝试
        try:
            fetched = await fetch_first_available(
                client,
                candidates,
                timeout=_DOWNLOAD_TIMEOUT,
                filename_hint=file_name,
                on_attempt=lambda url, kind, exc: download_attempts.append({"url": url, "kind": kind, "error": str(exc) or type(exc).__name__}),
            )
        except ImageFetchError as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(f"[MinerUTest] 全部候选地址下载失败 type={req.type} id={req.id} permanent={e.permanent}: {e.summary()}")
            return Fail(
                code="5000",
                msg=f"图片下载失败（permanent={e.permanent}，permanent=true 表示定时任务会把该记录熔断）：{e.summary()}",
                data={**base_data, "elapsed_ms": elapsed_ms, "download_attempts": download_attempts},
            )

        # ② 解析：backend 逐个回退，每个 backend 重试 _MAX_RETRIES 次
        for bi, backend in enumerate(_BACKENDS):
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    markdown = await convert_bytes_to_markdown(
                        fetched.data,
                        fetched.filename,
                        content_type=fetched.content_type,
                        client=client,
                        backend=backend,
                    )
                    parse_attempts.append({"backend": backend, "attempt": attempt, "status": "ok"})
                    break
                except MinerUError as e:
                    last_error = f"backend={backend} MinerU 错误: {e}"
                except Exception as e:  # noqa: BLE001 —— MinerU 服务 5xx / 连接抖动等
                    last_error = f"backend={backend} {type(e).__name__}: {e}"
                parse_attempts.append({"backend": backend, "attempt": attempt, "status": "error", "error": last_error[:300]})
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
            if markdown:
                break
            if bi < len(_BACKENDS) - 1:
                logger.info(f"[MinerUTest] backend={backend} 全部失败，回退到 {_BACKENDS[bi + 1]}")

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    result = {
        **base_data,
        "url": fetched.url,
        "used_fallback": fetched.url != candidates[0],
        "bytes": len(fetched.data),
        "content_type": fetched.content_type,
        "elapsed_ms": elapsed_ms,
        "download_attempts": download_attempts,
        "parse_attempts": parse_attempts,
    }

    if markdown:
        return Success(data={**result, "markdown": markdown})
    return Fail(code="5000", msg=f"MinerU 识别失败：{last_error}", data=result)
