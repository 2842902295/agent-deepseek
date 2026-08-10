"""
标准指标缓存查询接口 - 直接从 standard_cache_ind 查数据
"""

import json
import re
import time
from typing import AsyncGenerator, List

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.schemas.base import Success, Fail

router = APIRouter(prefix="/standard-ind", tags=["标准指标"])


@router.get("/taxonomy", summary="获取指标分类体系枚举")
async def get_ind_taxonomy():
    """
    从 mind_map parser 读取标准化对象分类体系，返回前端筛选/展示所需的枚举数据。
    结构：
      object_types: 对象类型列表
      categories_by_type: { 对象类型: [要素名称] }
      all_categories: 所有要素名称（去重）
      norm_classes: 规范类别列表
    """
    from app.langchain.agents.mind_map import TAXONOMY

    categories_by_type: dict[str, list[str]] = {}
    all_categories: list[str] = []
    seen: set[str] = set()

    for obj_type, groups in TAXONOMY.items():
        elements: list[str] = []
        for group_elements in groups.values():
            for e in group_elements:
                if e not in ("其他", "子主题"):
                    elements.append(e)
                    if e not in seen:
                        seen.add(e)
                        all_categories.append(e)
        categories_by_type[obj_type] = elements

    norm_classes_seen: set[str] = set()
    norm_classes: list[str] = []
    for groups in TAXONOMY.values():
        for group_name in groups:
            if group_name not in norm_classes_seen:
                norm_classes_seen.add(group_name)
                norm_classes.append(group_name)

    return Success(data={
        "object_types": list(TAXONOMY.keys()),
        "categories_by_type": categories_by_type,
        "all_categories": all_categories,
        "norm_classes": norm_classes,
    })


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_HTML_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """去除 HTML 标签（如章节正文中内嵌的 <img>/<table> 等），并折叠多余空白。"""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub("", text)).strip()


async def _build_clause_map(standard_no: str) -> dict:
    """
    构建 {title_no: 显示文本} 映射，用于富化 source_clause。
    """
    try:
        from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter
        pdf = await StandardJghPdf.filter(standard_no=standard_no).first()
        if not pdf:
            return {}
        chapters = await StandardJghPdfChapter.filter(main_task_id=pdf.main_task_id).values(
            "title_no", "title", "word"
        )
        result = {}
        for row in chapters:
            tn = (row["title_no"] or "").strip()
            if not tn:
                continue
            title = (row["title"] or "").strip()
            word = _strip_html(row["word"] or "")[:80].strip()
            if title != tn:
                result[tn] = title
            else:
                result[tn] = f"{tn} {word}" if word else tn
        return result
    except Exception as e:
        logger.warning(f"[clause_map] {standard_no} 查询失败: {e}")
        return {}


def _enrich_clause(clause_no: str, clause_map: dict) -> str:
    if not clause_no:
        return ""
    parts = [clause_map.get(p.strip(), p.strip()) for p in clause_no.split(",") if p.strip()]
    return "；".join(parts)


class ExtractBatchRequest(BaseModel):
    standard_nos: List[str]
    concurrency: int = 20
    run_remark: str = ""


async def _backfill_empty_run_ids():
    """
    幂等回填：为 standard_cache_ind / standard_cache_test 中 run_id 为空的旧记录
    按 (standard_no, create_time秒精度) 分组赋予合成 run_id。
    确保 list 接口能正确分行、前端能携带 run_id 查看历史版本。
    """
    import uuid as _uuid
    from collections import defaultdict as _dd
    from app.models.standard.cache_ind import StandardCacheInd
    from app.models.standard.cache_test import StandardCacheTest

    for Model in (StandardCacheInd, StandardCacheTest):
        # 查 run_id IS NULL 或 run_id='' 的记录（仅 is_valid=False 的历史记录，
        # is_valid=True 的由 _write_extraction_to_db 在写入时处理）
        orphans = await Model.filter(is_valid=False).all()
        orphans = [r for r in orphans if not r.run_id]
        if not orphans:
            continue

        # 按 (standard_no, create_time秒精度) 分组
        groups: dict = _dd(list)
        for r in orphans:
            tk = r.create_time.strftime("%Y%m%d%H%M%S") if r.create_time else "unknown"
            groups[(r.standard_no, tk)].append(r.id)

        for (sno, tk), ids in groups.items():
            syn = f"{sno}_legacy_{tk}_{_uuid.uuid4().hex[:4]}"
            await Model.filter(id__in=ids).update(run_id=syn)


@router.get("/list", summary="获取已提取指标的标准列表")
async def list_standard_ind(
        current: int = 1,
        size: int = 10,
        standard_no: str = "",
        standard_name: str = "",
):
    """
    查询 standard_cache_ind / standard_cache_test 中已提取结果的标准列表。
    按 run_id 分组，每次提取记录独立一行，支持对比不同版本。
    """
    try:
        # 回填历史坏数据（幂等，仅首次有实际写入）
        await _backfill_empty_run_ids()

        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest

        ind_query = StandardCacheInd.all()
        test_query = StandardCacheTest.all()

        if standard_no:
            ind_query = ind_query.filter(standard_no__icontains=standard_no)
            test_query = test_query.filter(standard_no__icontains=standard_no)
        if standard_name:
            ind_query = ind_query.filter(standard_name__icontains=standard_name)
            test_query = test_query.filter(standard_name__icontains=standard_name)

        all_ind_records = await ind_query.all()
        all_test_records = await test_query.all()

        # 按 run_id 分组，run_id 为空时退化为 standard_no
        grouped: dict = {}

        for r in all_ind_records:
            key = r.run_id or r.standard_no
            if key not in grouped:
                grouped[key] = {
                    "standard_no": r.standard_no,
                    "standard_name": r.standard_name or "",
                    "standard_structure_type": r.standard_structure_type or "",
                    "total_count": 0,
                    "test_count": 0,
                    "norm_class_counts": {},
                    "categories": set(),
                    "algorithm_version": r.algorithm_version or "",
                    "run_id": r.run_id or "",
                    "run_remark": r.run_remark or "",
                    "is_valid": r.is_valid,
                    "create_time": r.create_time.isoformat() if r.create_time else None,
                }
            g = grouped[key]
            g["total_count"] += 1
            nc = r.norm_class or ""
            if nc:
                g["norm_class_counts"][nc] = g["norm_class_counts"].get(nc, 0) + 1
            if r.indicator_category:
                g["categories"].add(r.indicator_category)
            if not g["standard_structure_type"] and r.standard_structure_type:
                g["standard_structure_type"] = r.standard_structure_type

        for r in all_test_records:
            key = r.run_id or r.standard_no
            if key not in grouped:
                grouped[key] = {
                    "standard_no": r.standard_no,
                    "standard_name": r.standard_name or "",
                    "standard_structure_type": "",
                    "total_count": 0,
                    "test_count": 0,
                    "norm_class_counts": {},
                    "categories": set(),
                    "algorithm_version": r.algorithm_version or "",
                    "run_id": r.run_id or "",
                    "run_remark": r.run_remark or "",
                    "is_valid": r.is_valid,
                    "create_time": r.create_time.isoformat() if r.create_time else None,
                }
            grouped[key]["test_count"] += 1

        result_list = []
        for g in grouped.values():
            if not g["standard_structure_type"]:
                if g["total_count"] > 0 and g["test_count"] > 0:
                    g["standard_structure_type"] = "has_ind_and_test"
                elif g["total_count"] > 0:
                    g["standard_structure_type"] = "has_ind_only"
                elif g["test_count"] > 0:
                    g["standard_structure_type"] = "has_test_only"
                else:
                    g["standard_structure_type"] = ""
            g["categories"] = sorted(g["categories"])
            result_list.append(g)

        result_list.sort(key=lambda x: x["create_time"] or "", reverse=True)

        total = len(result_list)
        offset = (current - 1) * size
        page_data = result_list[offset: offset + size]

        return Success(data={
            "list": page_data,
            "total": total,
            "current": current,
            "size": size,
        })
    except Exception as e:
        logger.error(f"[StandardInd] 获取标准列表失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


class DeleteStandardIndRunRequest(BaseModel):
    run_id: str


@router.delete("/delete-run", summary="删除单次提取记录（按 run_id）")
async def delete_standard_ind_run(request: DeleteStandardIndRunRequest):
    if not request.run_id:
        return Fail(msg="请传入 run_id")
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest
        from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel

        ind_ids = await StandardCacheInd.filter(run_id=request.run_id).values_list("id", flat=True)
        test_ids = await StandardCacheTest.filter(run_id=request.run_id).values_list("id", flat=True)

        if ind_ids:
            await StandardCacheIndTestRel.filter(ind_id__in=ind_ids).delete()
        if test_ids:
            await StandardCacheIndTestRel.filter(test_id__in=test_ids).delete()

        deleted_ind = await StandardCacheInd.filter(run_id=request.run_id).delete()
        deleted_test = await StandardCacheTest.filter(run_id=request.run_id).delete()

        return Success(data={"deleted": deleted_ind + deleted_test})
    except Exception as e:
        logger.error(f"[StandardInd] 删除 run 失败: {e}")
        return Fail(msg=f"删除失败: {str(e)}")





class DeleteStandardIndRequest(BaseModel):
    standard_nos: List[str]


@router.delete("/delete", summary="删除标准指标缓存")
async def delete_standard_ind(request: DeleteStandardIndRequest):
    """
    删除指定标准编号的全部指标缓存记录，支持批量传入多个标准编号
    """
    if not request.standard_nos:
        return Fail(msg="请传入标准编号")
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest
        from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel

        ind_ids = await StandardCacheInd.filter(standard_no__in=request.standard_nos).values_list("id", flat=True)
        test_ids = await StandardCacheTest.filter(standard_no__in=request.standard_nos).values_list("id", flat=True)

        if ind_ids:
            await StandardCacheIndTestRel.filter(ind_id__in=ind_ids).delete()
        if test_ids:
            await StandardCacheIndTestRel.filter(test_id__in=test_ids).delete()

        deleted_ind = await StandardCacheInd.filter(standard_no__in=request.standard_nos).delete()
        deleted_test = await StandardCacheTest.filter(standard_no__in=request.standard_nos).delete()

        return Success(data={"deleted": deleted_ind + deleted_test})
    except Exception as e:
        logger.error(f"[StandardInd] 删除失败: {e}")
        return Fail(msg=f"删除失败: {str(e)}")


@router.get("/indicators", summary="获取指定标准的全量指标")
async def get_standard_indicators(standard_no: str, run_id: str = ""):
    """
    从 standard_cache_ind 查询指定标准的全量指标。
    传 run_id 则查该批次；不传则查 is_valid=True 的最新版。
    """
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest
        from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel

        if run_id:
            records = await StandardCacheInd.filter(standard_no=standard_no, run_id=run_id).all()
        else:
            records = await StandardCacheInd.filter(standard_no=standard_no, is_valid=True).all()

        def _clause_sort_key(rec) -> tuple:
            clause = (rec.source_clause or "").split(",")[0].strip()
            try:
                return tuple(int(p) for p in clause.split(".") if p)
            except ValueError:
                return (999999,)

        records = sorted(records, key=_clause_sort_key)

        if not records:
            return Success(
                data={"indicators": [], "standard_no": standard_no, "standard_name": "", "standard_structure_type": ""})

        standard_name = records[0].standard_name or ""
        standard_structure_type = records[0].standard_structure_type or ""
        run_id = records[0].run_id or ""
        clause_map = await _build_clause_map(standard_no)

        # 查关联关系
        ind_id_list = [r.id for r in records]
        if run_id:
            rel_rows = await StandardCacheIndTestRel.filter(ind_id__in=ind_id_list, run_id=run_id).all()
        else:
            # 无 run_id（如比对流程写入的记录），直接按 ind_id 查
            rel_rows = await StandardCacheIndTestRel.filter(ind_id__in=ind_id_list).all() if ind_id_list else []
        test_ids = list({row.test_id for row in rel_rows})
        test_records = await StandardCacheTest.filter(id__in=test_ids).all() if test_ids else []
        test_map = {t.id: t for t in test_records}

        ind_to_tests: dict[int, list[dict]] = {}
        for rel in rel_rows:
            t = test_map.get(rel.test_id)
            if not t:
                continue
            ind_to_tests.setdefault(rel.ind_id, []).append({
                "id": t.id,
                "test_name": t.test_name,
                "method_desc": t.method_desc or "",
                "conditions": t.conditions or "",
                "preparation": t.preparation or "",
                "procedure": t.procedure or "",
                "acceptance": t.acceptance or "",
                "report_items": t.report_items or "",
                "source_clause": _enrich_clause(t.source_clause, clause_map),
            })

        indicators = []
        for r in records:
            ind: dict = {
                "id": r.id,
                "indicator_type": r.indicator_type,
                "standard_object": r.standard_object or "",
                "applicable_object": r.applicable_object or "",
                "object_type": r.object_type or "",
                "indicator_category": r.indicator_category or "",
                "norm_class": r.norm_class or "",
                "source_clause": _enrich_clause(r.source_clause, clause_map),
                "algorithm_version": r.algorithm_version or "",
                "indicator_object": r.indicator_object or r.experiment_name or "",
                "source_value": r.source_value or "",
                "linked_tests": ind_to_tests.get(r.id, []),
            }
            indicators.append(ind)

        return Success(data={
            "standard_no": standard_no,
            "standard_name": standard_name,
            "standard_structure_type": standard_structure_type,
            "indicators": indicators,
        })
    except Exception as e:
        logger.error(f"[StandardInd] 获取指标失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


@router.get("/tests", summary="获取指定标准的全量试验")
async def get_standard_tests(standard_no: str, run_id: str = ""):
    """
    从 standard_cache_test 查询指定标准的全量试验，并返回与指标的关联信息。
    传 run_id 则查该批次；不传则查 is_valid=True 的最新版。
    """
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest
        from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel

        if run_id:
            test_records = await StandardCacheTest.filter(standard_no=standard_no, run_id=run_id).all()
        else:
            test_records = await StandardCacheTest.filter(standard_no=standard_no, is_valid=True).all()

        if not test_records:
            return Success(data={"tests": [], "standard_no": standard_no, "standard_name": ""})

        def _clause_sort_key(rec) -> tuple:
            clause = (rec.source_clause or "").split(",")[0].strip()
            try:
                return tuple(int(p) for p in clause.split(".") if p)
            except ValueError:
                return (999999,)

        test_records = sorted(test_records, key=_clause_sort_key)

        standard_name = test_records[0].standard_name or ""
        run_id = test_records[0].run_id or ""
        clause_map = await _build_clause_map(standard_no)

        test_id_list = [r.id for r in test_records]
        rel_rows = await StandardCacheIndTestRel.filter(test_id__in=test_id_list, run_id=run_id).all() if run_id else []

        ind_ids = list({row.ind_id for row in rel_rows})
        ind_records = await StandardCacheInd.filter(id__in=ind_ids).all() if ind_ids else []
        ind_map = {r.id: r for r in ind_records}

        test_to_inds: dict[int, list[dict]] = {}
        for rel in rel_rows:
            ind = ind_map.get(rel.ind_id)
            if not ind:
                continue
            test_to_inds.setdefault(rel.test_id, []).append({
                "id": ind.id,
                "indicator_type": ind.indicator_type,
                "indicator_object": ind.indicator_object or "",
                "experiment_name": ind.experiment_name or "",
                "source_clause": _enrich_clause(ind.source_clause, clause_map),
            })

        tests = []
        for r in test_records:
            tests.append({
                "id": r.id,
                "test_name": r.test_name,
                "method_desc": r.method_desc or "",
                "conditions": r.conditions or "",
                "preparation": r.preparation or "",
                "procedure": r.procedure or "",
                "acceptance": r.acceptance or "",
                "report_items": r.report_items or "",
                "source_clause": _enrich_clause(r.source_clause, clause_map),
                "standard_object": r.standard_object or "",
                "applicable_object": r.applicable_object or "",
                "object_type": r.object_type or "",
                "indicator_category": r.indicator_category or "",
                "linked_indicators": test_to_inds.get(r.id, []),
            })

        return Success(data={
            "standard_no": standard_no,
            "standard_name": standard_name,
            "run_id": run_id,
            "tests": tests,
        })
    except Exception as e:
        logger.error(f"[StandardInd] 获取试验失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


@router.get("/all-indicators", summary="全量指标分页列表")
async def list_all_indicators(
        current: int = 1,
        size: int = 10,
        standard_no: str = "",
        norm_class: str = "",
        indicator_category: str = "",
        keyword: str = "",
        applicable_object: str = "",
        standard_object: str = "",
):
    """
    分页查询 standard_cache_ind 中所有有效指标，支持按标准编号、规范类别、分类、关键字筛选
    """
    try:
        from app.models.standard.cache_ind import StandardCacheInd

        query = StandardCacheInd.filter(is_valid=True)
        if standard_no:
            query = query.filter(standard_no__icontains=standard_no)
        if norm_class:
            query = query.filter(norm_class=norm_class)
        if applicable_object:
            query = query.filter(applicable_object__icontains=applicable_object)
        if standard_object:
            query = query.filter(standard_object__icontains=standard_object)
        if indicator_category:
            query = query.filter(indicator_category__icontains=indicator_category)
        if keyword:
            from tortoise.expressions import Q
            query = query.filter(
                Q(indicator_object__icontains=keyword) |
                Q(experiment_name__icontains=keyword) |
                Q(standard_object__icontains=keyword) |
                Q(source_value__icontains=keyword) |
                Q(source_result__icontains=keyword) |
                Q(source_clause__icontains=keyword)
            )

        total = await query.count()
        offset = (current - 1) * size
        records = await query.order_by("standard_no", "id").offset(offset).limit(size).all()

        # 按 standard_no 批量构建 clause_map
        standard_nos = list({r.standard_no for r in records})
        clause_maps: dict = {}
        for sno in standard_nos:
            clause_maps[sno] = await _build_clause_map(sno)

        items = []
        for r in records:
            cmap = clause_maps.get(r.standard_no, {})
            item: dict = {
                "id": r.id,
                "standard_no": r.standard_no,
                "standard_name": r.standard_name or "",
                "indicator_type": r.indicator_type,
                "object_type": r.object_type or "",
                "indicator_category": r.indicator_category or "",
                "norm_class": r.norm_class or "",
                "standard_object": r.standard_object or "",
                "applicable_object": r.applicable_object or "",
                "source_clause": _enrich_clause(r.source_clause, cmap),
                "algorithm_version": r.algorithm_version or "",
            }
            if r.indicator_type == "static":
                item["indicator_object"] = r.indicator_object or ""
                item["source_value"] = r.source_value or ""
            else:
                item["experiment_name"] = r.experiment_name or ""
                item["source_result"] = r.source_result or ""
                item["source_input_params"] = r.source_input_params or ""
            items.append(item)

        return Success(data={
            "list": items,
            "total": total,
            "current": current,
            "size": size,
        })
    except Exception as e:
        logger.error(f"[StandardInd] 获取全量指标失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


@router.post("/extract-batch", summary="批量提取标准指标（流式SSE）")
async def extract_batch_stream(request: ExtractBatchRequest):
    """
    逐个调用 indicator_extraction_agent.extract_indicators 提取指标，
    提取完成后写入 standard_cache_ind，通过 SSE 实时推送进度。

    每次提取生成唯一 run_id，旧 run 的记录保留（is_valid=False），便于对比调试。

    SSE 事件类型：
    - start       { total }
    - processing  { standard_no, index, total }
    - progress    { standard_no, index, total, count, run_id }
    - error       { standard_no, index, total, message }
    - done        { total, success, failed }
    """
    import asyncio
    from app.langchain.agents.tasks.indicator.indicator_extraction_agent import extract_indicators
    from app.models.standard.jgh_pdf import StandardJghPdf

    standard_nos = [s.strip() for s in request.standard_nos if s.strip()]
    run_remark = (request.run_remark or "").strip()
    queue: asyncio.Queue = asyncio.Queue()

    async def _process_one(semaphore: asyncio.Semaphore, index: int, standard_no: str, total: int, counter: dict):
        async with semaphore:
            await queue.put({"type": "processing", "standard_no": standard_no, "index": index + 1, "total": total})
            try:
                standard_name = ""
                try:
                    pdf_row = await StandardJghPdf.filter(standard_no=standard_no).first()
                    if pdf_row:
                        standard_name = pdf_row.cname or ""
                except Exception:
                    pass

                t0 = time.time()
                result = await extract_indicators(standard_no, standard_name)
                elapsed = time.time() - t0

                run_id = await _write_extraction_to_db(
                    standard_no=standard_no,
                    standard_name=standard_name,
                    result=result,
                    run_remark=run_remark,
                    elapsed=elapsed,
                    algorithm_version="v3",
                )

                counter["success"] += 1
                await queue.put({
                    "type": "progress",
                    "standard_no": standard_no,
                    "index": index + 1,
                    "total": total,
                    "count": len(result.indicators),
                    "test_count": len(result.tests),
                    "standard_structure_type": result.standard_structure_type,
                    "run_id": run_id,
                })

            except Exception as e:
                logger.exception(f"[指标拆解] {standard_no} 失败")
                counter["failed"] += 1
                await queue.put({
                    "type": "error",
                    "standard_no": standard_no,
                    "index": index + 1,
                    "total": total,
                    "message": str(e),
                })

    async def _run():
        total = len(standard_nos)
        await queue.put({"type": "start", "total": total})
        counter = {"success": 0, "failed": 0}

        semaphore = asyncio.Semaphore(request.concurrency)
        tasks = [_process_one(semaphore, i, sno, total, counter) for i, sno in enumerate(standard_nos)]
        await asyncio.gather(*tasks)

        await queue.put({"type": "done", "total": total, "success": counter["success"], "failed": counter["failed"]})
        await queue.put(None)  # 哨兵，通知生成器结束

    # 后台任务独立运行，不受 SSE 连接生命周期影响
    asyncio.create_task(_run())

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        except asyncio.CancelledError:
            # 前端断开连接，生成器被取消，后台任务继续运行
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


import re as _re_global
_re_non_alnum_global = _re_global.compile(r"[^\w一-鿿]+")


def _norm_name(s: str) -> str:
    return _re_non_alnum_global.sub("", s).lower()


async def _write_extraction_to_db(
    standard_no: str,
    standard_name: str,
    result,
    run_remark: str,
    elapsed: float,
    algorithm_version: str = "v3",
) -> str:
    """
    将 ExtractionOutput 写入 standard_cache_ind / standard_cache_test / standard_cache_ind_test_rel。
    旧记录置为 is_valid=False（保留历史），返回本次 run_id。
    """
    import uuid as _uuid
    from app.models.standard.cache_ind import StandardCacheInd
    from app.models.standard.cache_test import StandardCacheTest
    from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel

    run_id = f"{standard_no}_{_uuid.uuid4().hex[:8]}"

    # ── 回填旧记录中缺失的 run_id（legacy 数据兼容）──────────────────────────
    # 旧版本写入的记录 run_id 为空，按 create_time（秒精度）分组视为同一次提取，
    # 赋予合成 run_id，使 list 接口能正确分行、前端能携带 run_id 查看历史版本。
    from collections import defaultdict as _dd
    for _Model in (StandardCacheInd, StandardCacheTest):
        _candidates = await _Model.filter(standard_no=standard_no, is_valid=True).all()
        _orphans = [r for r in _candidates if not r.run_id]
        if _orphans:
            _groups = _dd(list)
            for _r in _orphans:
                _tk = _r.create_time.strftime("%Y%m%d%H%M%S") if _r.create_time else "unknown"
                _groups[_tk].append(_r.id)
            for _tk, _ids in _groups.items():
                _syn = f"{standard_no}_legacy_{_tk}_{_uuid.uuid4().hex[:4]}"
                await _Model.filter(id__in=_ids).update(run_id=_syn)

    await StandardCacheInd.filter(standard_no=standard_no, is_valid=True).update(is_valid=False)
    await StandardCacheTest.filter(standard_no=standard_no, is_valid=True).update(is_valid=False)

    written_ind: dict[str, int] = {}
    for ind in result.indicators:
        ind_record = await StandardCacheInd.create(
            standard_no=standard_no,
            standard_name=standard_name or None,
            run_id=run_id,
            run_remark=run_remark or None,
            standard_structure_type=result.standard_structure_type or None,
            indicator_type=ind.indicator_type,
            standard_object=ind.standard_object or None,
            applicable_object=ind.applicable_object or None,
            indicator_category=ind.indicator_category or None,
            norm_class=ind.norm_class or None,
            indicator_object=ind.indicator_object or None,
            source_value=ind.source_value or None,
            source_clause=ind.source_clause or None,
            object_type=ind.object_type or None,
            extraction_time=elapsed,
            is_valid=True,
            algorithm_version=algorithm_version,
        )
        written_ind[_norm_name(ind.indicator_object)] = ind_record.id

    _seen_test_keys: set[str] = set()
    _unique_tests = []
    for _t in result.tests:
        _k = _norm_name(_t.test_name)
        if _k not in _seen_test_keys:
            _seen_test_keys.add(_k)
            _unique_tests.append(_t)

    for test in _unique_tests:
        test_record = await StandardCacheTest.create(
            standard_no=standard_no,
            standard_name=standard_name or None,
            run_id=run_id,
            run_remark=run_remark or None,
            test_name=test.test_name,
            method_desc=test.method_desc or None,
            conditions=test.conditions or None,
            preparation=test.preparation or None,
            procedure=test.procedure or None,
            acceptance=test.acceptance or None,
            report_items=test.report_items or None,
            source_clause=test.source_clause or None,
            standard_object=test.standard_object or None,
            applicable_object=test.applicable_object or None,
            object_type=test.object_type or None,
            indicator_category=test.indicator_category or None,
            is_valid=True,
            algorithm_version=algorithm_version,
        )
        for ind in test.indicators:
            ind_id = written_ind.get(_norm_name(ind.indicator_object))
            if ind_id is None:
                continue
            await StandardCacheIndTestRel.create(
                ind_id=ind_id,
                test_id=test_record.id,
                run_id=run_id,
            )

    return run_id


@router.post("/extract-batch-fast", summary="批量提取标准指标-快速版（流式SSE）")
async def extract_batch_fast_stream(request: ExtractBatchRequest):
    """
    快速版指标拆解接口。

    不使用多轮 ReAct Agent，全程单次 LLM 调用 + JSON 解析：
      - 快路径（compact ≤ 阈值）：1 次 LLM one-shot 完成全部提取
      - 慢路径：1 次分组规划 + N 组并行指标提取 + 并行试验提取

    写库结果、SSE 事件格式与 /extract-batch 完全一致，可互替使用。

    SSE 事件类型：
    - start       { total }
    - processing  { standard_no, index, total }
    - progress    { standard_no, index, total, count, test_count, run_id, path }
    - error       { standard_no, index, total, message }
    - done        { total, success, failed }
    """
    import asyncio
    from app.langchain.agents.tasks.indicator.fast_extraction import fast_extract_indicators
    from app.models.standard.jgh_pdf import StandardJghPdf

    standard_nos = [s.strip() for s in request.standard_nos if s.strip()]
    run_remark = (request.run_remark or "").strip()
    queue: asyncio.Queue = asyncio.Queue()

    async def _process_one(semaphore: asyncio.Semaphore, index: int, standard_no: str, total: int, counter: dict):
        async with semaphore:
            await queue.put({"type": "processing", "standard_no": standard_no, "index": index + 1, "total": total})
            try:
                standard_name = ""
                try:
                    pdf_row = await StandardJghPdf.filter(standard_no=standard_no).first()
                    if pdf_row:
                        standard_name = pdf_row.cname or ""
                except Exception:
                    pass

                t0 = time.time()
                from app.settings import APP_SETTINGS as _settings
                result = await fast_extract_indicators(
                    standard_no, standard_name,
                    compact_limit=_settings.SMART_V2_FAST_BATCH_COMPACT_LIMIT,
                )
                elapsed = time.time() - t0

                run_id = await _write_extraction_to_db(
                    standard_no=standard_no,
                    standard_name=standard_name,
                    result=result,
                    run_remark=run_remark,
                    elapsed=elapsed,
                    algorithm_version="v3-fast",
                )

                counter["success"] += 1
                await queue.put({
                    "type": "progress",
                    "standard_no": standard_no,
                    "index": index + 1,
                    "total": total,
                    "count": len(result.indicators),
                    "test_count": len(result.tests),
                    "standard_structure_type": result.standard_structure_type,
                    "run_id": run_id,
                })

            except Exception as e:
                logger.exception(f"[指标拆解-快速] {standard_no} 失败")
                counter["failed"] += 1
                await queue.put({
                    "type": "error",
                    "standard_no": standard_no,
                    "index": index + 1,
                    "total": total,
                    "message": str(e),
                })

    async def _run():
        total = len(standard_nos)
        await queue.put({"type": "start", "total": total})
        counter = {"success": 0, "failed": 0}

        semaphore = asyncio.Semaphore(request.concurrency)
        tasks = [_process_one(semaphore, i, sno, total, counter) for i, sno in enumerate(standard_nos)]
        await asyncio.gather(*tasks)

        await queue.put({"type": "done", "total": total, "success": counter["success"], "failed": counter["failed"]})
        await queue.put(None)

    asyncio.create_task(_run())

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


__all__ = ["router"]
