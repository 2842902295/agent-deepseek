"""
标准化对象视角接口 — 以 standard_object 为维度聚合指标数据
"""

from fastapi import APIRouter
from loguru import logger

from app.schemas.base import Success

router = APIRouter(prefix="/standard-obj", tags=["标准化对象"])


@router.get("/list", summary="按标准化对象分组统计")
async def list_standard_obj(
        current: int = 1,
        size: int = 10,
        keyword: str = "",
        norm_class: str = "",
        indicator_category: str = "",
):
    """
    从 standard_cache_ind（is_valid=True）按 standard_object + applicable_object 分组，
    统计每个对象下的标准数、指标数、norm_class 分布、指标分类。
    keyword 同时匹配 standard_object 和 applicable_object。
    """
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from tortoise.expressions import Q

        query = StandardCacheInd.filter(is_valid=True)
        if keyword:
            query = query.filter(
                Q(standard_object__icontains=keyword) | Q(applicable_object__icontains=keyword)
            )
        if norm_class:
            query = query.filter(norm_class=norm_class)
        if indicator_category:
            query = query.filter(indicator_category__icontains=indicator_category)

        records = await query.all()

        grouped: dict = {}
        for r in records:
            obj = r.standard_object or "（未分类）"
            app_obj = r.applicable_object or ""
            key = (obj, app_obj)
            if key not in grouped:
                # match_priority: 0=standard_object命中, 1=仅applicable_object命中
                so_hit = keyword and keyword.lower() in obj.lower()
                grouped[key] = {
                    "standard_object": obj,
                    "applicable_object": app_obj,
                    "standard_nos": set(),
                    "total_count": 0,
                    "norm_class_counts": {},
                    "categories": set(),
                    "match_priority": 0 if (not keyword or so_hit) else 1,
                }
            g = grouped[key]
            g["standard_nos"].add(r.standard_no)
            g["total_count"] += 1
            if r.norm_class:
                g["norm_class_counts"][r.norm_class] = g["norm_class_counts"].get(r.norm_class, 0) + 1
            if r.indicator_category:
                g["categories"].add(r.indicator_category)

        result_list = []
        for g in grouped.values():
            result_list.append({
                "standard_object": g["standard_object"],
                "applicable_object": g["applicable_object"],
                "standard_count": len(g["standard_nos"]),
                "standard_nos": sorted(g["standard_nos"]),
                "total_count": g["total_count"],
                "norm_class_counts": g["norm_class_counts"],
                "categories": sorted(g["categories"]),
                "_match_priority": g["match_priority"],
            })

        result_list.sort(key=lambda x: (x["_match_priority"], -x["total_count"]))
        for item in result_list:
            del item["_match_priority"]

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
        logger.error(f"[StandardObj] 获取对象列表失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


@router.get("/indicators", summary="获取指定标准化对象的全量指标")
async def get_obj_indicators(standard_object: str):
    """
    查询指定 standard_object 下所有 is_valid=True 的指标和试验，
    返回结构与 /standard-ind/indicators 相同，但 standard_no 字段保留用于区分来源。
    """
    try:
        from app.models.standard.cache_ind import StandardCacheInd
        from app.models.standard.cache_test import StandardCacheTest
        from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel
        from app.api.v1.ai.standard_ind import _build_clause_map, _enrich_clause

        ind_records = await StandardCacheInd.filter(
            standard_object=standard_object,
            is_valid=True,
        ).all()

        def _clause_sort_key(rec) -> tuple:
            clause = (rec.source_clause or "").split(",")[0].strip()
            try:
                return tuple(int(p) for p in clause.split(".") if p)
            except ValueError:
                return (999999,)

        ind_records = sorted(ind_records, key=_clause_sort_key)

        if not ind_records:
            return Success(data={
                "standard_object": standard_object,
                "standard_no": "",
                "standard_name": "",
                "standard_structure_type": "",
                "indicators": [],
            })

        # 按 standard_no 批量构建 clause_map
        standard_nos = list({r.standard_no for r in ind_records})
        clause_maps: dict = {}
        for sno in standard_nos:
            clause_maps[sno] = await _build_clause_map(sno)

        # 查关联试验
        ind_id_list = [r.id for r in ind_records]
        # 每条记录用自己的 run_id 查关联
        run_ids = list({r.run_id for r in ind_records if r.run_id})
        rel_rows = await StandardCacheIndTestRel.filter(ind_id__in=ind_id_list).all() if ind_id_list else []
        test_ids = list({row.test_id for row in rel_rows})
        test_records = await StandardCacheTest.filter(id__in=test_ids).all() if test_ids else []
        test_map = {t.id: t for t in test_records}

        ind_to_tests: dict[int, list[dict]] = {}
        for rel in rel_rows:
            t = test_map.get(rel.test_id)
            if not t:
                continue
            cmap = clause_maps.get(t.standard_no, {})
            ind_to_tests.setdefault(rel.ind_id, []).append({
                "id": t.id,
                "test_name": t.test_name,
                "method_desc": t.method_desc or "",
                "conditions": t.conditions or "",
                "preparation": t.preparation or "",
                "procedure": t.procedure or "",
                "acceptance": t.acceptance or "",
                "report_items": t.report_items or "",
                "source_clause": _enrich_clause(t.source_clause, cmap),
            })

        indicators = []
        for r in ind_records:
            cmap = clause_maps.get(r.standard_no, {})
            indicators.append({
                "id": r.id,
                "indicator_type": r.indicator_type,
                "standard_no": r.standard_no,
                "standard_object": r.standard_object or "",
                "applicable_object": r.applicable_object or "",
                "object_type": r.object_type or "",
                "indicator_category": r.indicator_category or "",
                "norm_class": r.norm_class or "",
                "source_clause": _enrich_clause(r.source_clause, cmap),
                "algorithm_version": r.algorithm_version or "",
                "indicator_object": r.indicator_object or r.experiment_name or "",
                "source_value": r.source_value or "",
                "linked_tests": ind_to_tests.get(r.id, []),
            })

        # 试验：查该对象下所有 is_valid=True 的试验
        test_records_all = await StandardCacheTest.filter(
            standard_object=standard_object,
            is_valid=True,
        ).all()
        test_records_all = sorted(test_records_all, key=_clause_sort_key)

        # 查试验关联指标
        test_id_list = [r.id for r in test_records_all]
        rel_rows2 = await StandardCacheIndTestRel.filter(test_id__in=test_id_list).all() if test_id_list else []
        ind_ids2 = list({row.ind_id for row in rel_rows2})
        ind_records2 = await StandardCacheInd.filter(id__in=ind_ids2).all() if ind_ids2 else []
        ind_map2 = {r.id: r for r in ind_records2}

        test_to_inds: dict[int, list[dict]] = {}
        for rel in rel_rows2:
            ind = ind_map2.get(rel.ind_id)
            if not ind:
                continue
            test_to_inds.setdefault(rel.test_id, []).append({
                "id": ind.id,
                "indicator_type": ind.indicator_type,
                "indicator_object": ind.indicator_object or "",
                "experiment_name": ind.experiment_name or "",
                "source_clause": "",
            })

        tests = []
        for r in test_records_all:
            cmap = clause_maps.get(r.standard_no, {})
            tests.append({
                "id": r.id,
                "test_name": r.test_name,
                "method_desc": r.method_desc or "",
                "conditions": r.conditions or "",
                "preparation": r.preparation or "",
                "procedure": r.procedure or "",
                "acceptance": r.acceptance or "",
                "report_items": r.report_items or "",
                "source_clause": _enrich_clause(r.source_clause, cmap),
                "standard_no": r.standard_no,
                "standard_object": r.standard_object or "",
                "applicable_object": r.applicable_object or "",
                "object_type": r.object_type or "",
                "indicator_category": r.indicator_category or "",
                "linked_indicators": test_to_inds.get(r.id, []),
            })

        return Success(data={
            "standard_object": standard_object,
            "standard_no": "",
            "standard_name": standard_object,
            "standard_structure_type": "has_ind_and_test" if tests else "has_ind_only",
            "indicators": indicators,
            "tests": tests,
        })
    except Exception as e:
        logger.error(f"[StandardObj] 获取对象指标失败: {e}")
        return Success(data=None, msg=f"查询失败: {str(e)}")


__all__ = ["router"]
