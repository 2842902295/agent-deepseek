"""
标准化对象关系接口

  POST /standard-obj-rel/build      — 遍历 standard_base_info.std_obj，批量推断并写入 standard_obj_rel（SSE）
  GET  /standard-obj-rel/graph      — 以指定对象为根节点，返回 N 层关联图数据（nodes + edges）
"""

import asyncio
import json
from collections import deque
from typing import AsyncGenerator, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.schemas.base import Success

router = APIRouter(prefix="/standard-obj-rel", tags=["标准化对象关系"])


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class BuildObjRelRequest(BaseModel):
    limit: Optional[int] = None
    concurrency: int = 20
    order_by: str = "create_time"  # create_time | update_time | std_obj，前缀 - 表示降序，如 -create_time
    # 筛选条件（对应 standard_base_info 字段）
    std_domain: Optional[str] = None
    std_field: Optional[str] = None
    std_type: Optional[str] = None
    state: Optional[str] = None
    nat_cat: Optional[str] = None
    cname: Optional[str] = None


@router.post("/build", summary="批量构建对象关系（流式SSE）")
async def build_obj_rel(request: BuildObjRelRequest):
    """
    遍历 standard_base_info.std_obj，并发调用 infer_obj_rels 推断关系，结果写入 standard_obj_rel。
    通过 SSE 实时推送进度。

    SSE 事件类型：
    - start       { total }
    - processing  { obj, index, total }
    - progress    { obj, index, total, count }
    - error       { obj, index, total, message }
    - done        { total, success, failed }
    """
    from app.langchain.agents.tasks.object.obj_rel_infer_agent import infer_obj_rels
    from app.models.standard.base_info import StandardBaseInfo
    from app.models.standard.obj_rel import StandardObjRel

    queue: asyncio.Queue = asyncio.Queue()

    async def _get_objects() -> list[str]:
        query = StandardBaseInfo.filter(deleted=False).exclude(std_obj=None)
        if request.std_domain:
            query = query.filter(std_domain=request.std_domain)
        if request.std_field:
            query = query.filter(std_field=request.std_field)
        if request.std_type:
            query = query.filter(std_type=request.std_type)
        if request.state:
            query = query.filter(state=request.state)
        if request.nat_cat:
            query = query.filter(nat_cat__icontains=request.nat_cat)
        if request.cname:
            query = query.filter(cname__icontains=request.cname)

        order_field = request.order_by.lstrip("-")
        if request.order_by.startswith("-"):
            query = query.order_by(f"-{order_field}")
        else:
            query = query.order_by(order_field)

        rows = await query.values_list("standard_no", "std_obj")

        # 按标准排序保留去重后的对象顺序，同时记录第一个出现的 standard_no
        seen: set[str] = set()
        objs: list[tuple[str, str]] = []  # (obj_name, standard_no)
        for standard_no, raw in rows:
            if raw:
                for part in str(raw).split("、"):
                    part = part.strip()
                    if part and part not in seen:
                        seen.add(part)
                        objs.append((part, standard_no or ""))

        # 去除已处理对象后再应用 limit
        done = set(await StandardObjRel.filter(deleted=False).distinct().values_list("subj_obj", flat=True))
        result = [(o, sno) for o, sno in objs if o not in done]
        if request.limit:
            result = result[: request.limit]
        return result

    async def _process_one(semaphore: asyncio.Semaphore, obj: str, standard_no: str, index: int, total: int,
                           counter: dict):
        async with semaphore:
            await queue.put({"type": "processing", "obj": obj, "index": index + 1, "total": total})
            try:
                result = await infer_obj_rels(obj, standard_no)

                saved = 0
                for rel in result.relations:
                    await StandardObjRel.create(
                        subj_obj=rel.subj_obj,
                        rel_type=rel.rel_type,
                        obj_obj=rel.obj_obj,
                        rel_desc=rel.rel_desc or None,
                        src_type="agent",
                        src_standard_no=rel.src_standard_no,
                        src_clause_id=rel.src_clause_id or None,
                        confidence=rel.confidence,
                    )
                    saved += 1

                counter["success"] += 1
                await queue.put({"type": "progress", "obj": obj, "index": index + 1, "total": total, "count": saved})

            except Exception as e:
                logger.exception(f"[对象关系] {obj} 推断失败")
                counter["failed"] += 1
                await queue.put({"type": "error", "obj": obj, "index": index + 1, "total": total, "message": str(e)})

    async def _run():
        objs = await _get_objects()
        total = len(objs)
        await queue.put({"type": "start", "total": total})
        counter = {"success": 0, "failed": 0}

        semaphore = asyncio.Semaphore(request.concurrency)
        tasks = [_process_one(semaphore, obj, sno, i, total, counter) for i, (obj, sno) in enumerate(objs)]
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


@router.get("/graph", summary="获取对象关系图数据")
async def get_obj_rel_graph(
        subj_obj: str,
        depth: int = 2,
        rel_type: Optional[str] = None,  # 关系类型过滤，如：包含
        confidence: Optional[str] = None,  # 置信度过滤，如：high
        src_type: Optional[str] = None,  # 来源类型过滤，如：agent
):
    """
    以 subj_obj 为根节点，BFS 展开 depth 层关联，返回 G6 图数据（nodes + edges）。

    - depth：展开层数，默认2层
    - rel_type：过滤关系类型（包含 | 属于 | 细分 | 归属 | 配套）
    - confidence：过滤置信度（high | medium | low）
    - src_type：过滤来源类型（agent | seed 等）
    """
    from app.models.standard.obj_rel import StandardObjRel

    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}  # key=edge_id 去重
    visited_objs: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(subj_obj, 0)])

    while queue:
        current_obj, current_depth = queue.popleft()
        if current_obj in visited_objs:
            continue
        visited_objs.add(current_obj)

        nodes[current_obj] = {
            "id": current_obj,
            "label": current_obj,
            "isRoot": current_obj == subj_obj,
        }

        if depth != -1 and current_depth >= depth:
            continue

        query = StandardObjRel.filter(subj_obj=current_obj, deleted=False)
        if rel_type:
            query = query.filter(rel_type=rel_type)
        if confidence:
            query = query.filter(confidence=confidence)
        if src_type:
            query = query.filter(src_type=src_type)

        rels = await query.all()
        for r in rels:
            edge_id = f"{r.subj_obj}__{r.rel_type}__{r.obj_obj}"
            edges[edge_id] = {
                "id": edge_id,
                "source": r.subj_obj,
                "target": r.obj_obj,
                "label": r.rel_type,
                "confidence": r.confidence,
            }
            if r.obj_obj not in visited_objs:
                queue.append((r.obj_obj, current_depth + 1))
                # 提前占位，避免同层重复加入队列
                nodes.setdefault(r.obj_obj, {"id": r.obj_obj, "label": r.obj_obj, "isRoot": False})

    return Success(data={
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    })


__all__ = ["router"]
