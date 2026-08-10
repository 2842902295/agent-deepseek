"""
MinerU 单条测试接口

用于单独调试定时任务中图片识别失败的记录。
接受 type（table / foluma）和 id，调用 MinerU 解析后直接返回结果，
不写入数据库、不写入日志表。
"""

from __future__ import annotations

import time
from typing import Literal

import httpx
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.models.standard.jgh_pdf import StandardJghPdfFormula, StandardJghPdfTable
from app.schemas.base import Fail, Success
from app.settings.config import settings
from app.utils.mineru import MinerUError, convert_to_markdown

router = APIRouter(prefix="/mineru-test", tags=["MinerU测试"])

# 与 scheduler.py 共用 settings.JGH_IMAGE_BASE_URL
_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL
_FALLBACK_HOST = "http://dzsy.iyunwen.com"
_BACKENDS = ("vlm-engine", "pipeline")
_MAX_RETRIES = 3
_RETRY_DELAY = 5


def _is_download_error(exc: Exception) -> bool:
    """判断是否为图片下载阶段的错误（HTTP 4xx/5xx、连接超时等）。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)):
        return True
    return False


class MinerUTestRequest(BaseModel):
    type: Literal["table", "foluma"] = Field(..., description="类型：table（表格）或 foluma（公式）")
    id: int = Field(..., description="standard_jgh_pdf_table 或 standard_jgh_pdf_formula 的主键 ID")


@router.post("")
async def test_mineru_recognition(req: MinerUTestRequest):
    """
    单条测试 MinerU 图片识别（模拟定时任务逻辑，含 backend 降级 + 重试）。

    - type: table → 查 standard_jgh_pdf_table；foluma → 查 standard_jgh_pdf_formula
    - id: 对应表的主键
    - 结果不写入数据库和日志表，仅返回
    """
    model_cls = StandardJghPdfTable if req.type == "table" else StandardJghPdfFormula
    table_name = model_cls.Meta.table

    record = await model_cls.filter(id=req.id).first()
    if not record:
        return Fail(code="4004", msg=f"未找到记录：{table_name} id={req.id}")

    file_name = record.file_name
    image_path = record.image  # 例：/oss/privateDomain/20230111/2022/xxx.jpg

    if not file_name and not image_path:
        return Fail(code="4000", msg=f"该记录 file_name 和 image 均为空：{table_name} id={req.id}")

    primary_url = (_IMAGE_BASE_URL + file_name.lstrip("/")) if file_name else None
    fallback_url = (_FALLBACK_HOST + image_path) if image_path else None
    # 优先使用 primary_url，若不存在则直接用 fallback_url 作为主地址
    url = primary_url or fallback_url
    logger.info(f"[MinerUTest] 开始测试 type={req.type} id={req.id} url={url}"
                + (f" fallback={fallback_url}" if fallback_url and primary_url else ""))

    t0 = time.monotonic()
    attempts_log: list[dict] = []
    last_error = ""
    success = False
    markdown = ""
    used_fallback = False

    for bi, backend in enumerate(_BACKENDS):
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                markdown = await convert_to_markdown(url, backend=backend)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                attempts_log.append({
                    "backend": backend,
                    "attempt": attempt,
                    "status": "ok",
                    "url": url,
                })
                success = True
                break
            except MinerUError as e:
                last_error = str(e)
                attempts_log.append({
                    "backend": backend,
                    "attempt": attempt,
                    "status": "mineru_error",
                    "error": str(e),
                    "url": url,
                })
                # MinerUError 中包含下载阶段的错误（响应内容为空等）→ 触发 fallback
                if "图片下载失败" in str(e) and not used_fallback and fallback_url:
                    logger.info(
                        f"[MinerUTest] 图片下载失败（内容为空），切换到备用地址：{fallback_url}"
                    )
                    url = fallback_url
                    used_fallback = True
                    continue
                logger.warning(
                    f"[MinerUTest] MinerU 失败 backend={backend} "
                    f"attempt={attempt}/{_MAX_RETRIES}: {e}"
                )
                if attempt < _MAX_RETRIES:
                    import asyncio
                    await asyncio.sleep(_RETRY_DELAY)
            except Exception as e:
                last_error = str(e)
                attempts_log.append({
                    "backend": backend,
                    "attempt": attempt,
                    "status": "exception",
                    "error": str(e),
                    "url": url,
                })
                # 下载失败且尚未尝试备用地址 → 切换到 fallback URL 继续
                if _is_download_error(e) and not used_fallback and fallback_url:
                    logger.info(
                        f"[MinerUTest] 图片下载失败，切换到备用地址：{fallback_url}"
                    )
                    url = fallback_url
                    used_fallback = True
                    continue  # 用新 URL 重试，不消耗 backend 降级
                logger.warning(
                    f"[MinerUTest] 异常 backend={backend} "
                    f"attempt={attempt}/{_MAX_RETRIES}: {e}"
                )
                break  # 非下载异常或已用过 fallback，不回退，直接终止
        if success:
            break
        # 当前 backend 全部重试失败，回退到下一个
        if bi < len(_BACKENDS) - 1:
            logger.info(
                f"[MinerUTest] backend={backend} 全部失败，回退到 {_BACKENDS[bi + 1]}"
            )

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if success:
        return Success(data={
            "type": req.type,
            "id": req.id,
            "table": table_name,
            "file_name": file_name,
            "url": url,
            "used_fallback": used_fallback,
            "markdown": markdown,
            "elapsed_ms": elapsed_ms,
            "attempts": attempts_log,
        })
    else:
        return Fail(
            code="5000",
            msg=f"MinerU 识别失败：{last_error}",
            data={
                "type": req.type,
                "id": req.id,
                "table": table_name,
                "file_name": file_name,
                "url": url,
                "used_fallback": used_fallback,
                "elapsed_ms": elapsed_ms,
                "attempts": attempts_log,
                "last_error": last_error,
            },
        )
