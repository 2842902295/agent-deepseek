"""
批量标准查重 Agent（嵌入式快速版 / 任务级）

与 standard_deduplication_agent.py 的输入输出完全一致，区别只在召回方式：

- 标签（标准化对象一致 / 通用专用关系 / 适用范围重叠）和 llm_score 仍由 LLM 判定
- 同系列标准仍由代码规则强制
- 候选来源不再让 agent 自主写 SQL/调向量，而是系统直接用向量检索预召回好
  （按"标准名称"召回一轮 + 按"适用范围"召回一轮，合并去重），把候选清单
  喂给 analyzer agent 一次性打标

效果略差但流程明显变短，适合大批量场景（如 > 200 条）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Dict, List, Optional, Tuple

from langchain.tools import tool
from loguru import logger

from app.langchain.agents.tasks.standard.standard_deduplication_agent import (
    SimilarStandardOutput,
    _correct_same_series_tag,
    _dedup_candidates,
    _filter_all_false_tags,
    _filter_candidates,
    _finalize_batch,
    _recall_same_series_candidates,
    _recalc_need_attention,
    _verify_candidates_exist,
)


# ── 向量召回（系统侧预召回，不暴露给 LLM） ────────────────────────────────────

# 模块内单例锁：保证多个批量任务同时启动时，构建只触发一次
_index_warmup_lock = asyncio.Lock()

# 等待构建完成的最长时间（秒）。首次全量构建可能远超此时长，超时即整批明确报错
_WAIT_READY_TIMEOUT_S = 600


async def _meta_lib_state() -> Tuple[str, str]:
    """standard_meta 库的派生状态，返回 (状态, 说明)。"""
    from app.services.vector_hub.library_service import get_library
    from app.services.vector_hub.state import derive_lib_state

    lib = await get_library("standard_meta")
    if lib is None:
        return "missing", "系统向量库未初始化（服务重启中或初始化失败）"
    return derive_lib_state(lib), ""


async def _ensure_vector_index_ready() -> Tuple[bool, str]:
    """
    确保 standard_meta 系统向量库可用。返回 (是否就绪, 不就绪原因)。

    - ready    → 直接就绪
    - building → 轮询等待（最多 10 分钟）
    - empty    → 触发一次构建并轮询等待
    - stale    → 明确报错（换过 embed 块必须先重建，绝不拿陈旧向量凑合）
    - error    → 明确报错（附带 last_error）
    """
    from app.services.vector_hub.build import build_library, is_building
    from app.services.vector_hub.library_service import get_library

    state, why = await _meta_lib_state()
    if state == "ready":
        return True, ""

    if state in ("stale", "error", "missing"):
        if state == "missing":
            return False, why
        lib = await get_library("standard_meta")
        detail = (lib.last_error or "") if (lib is not None and state == "error") else ""
        msg = (
            "标准向量库已陈旧（切换过 embed 模型块），请在向量库管理页重建后再查重"
            if state == "stale"
            else f"标准向量库上次构建失败：{detail or '未知错误'}"
        )
        return False, msg

    # empty / building：empty 先触发构建，再统一轮询等待
    if state == "empty":
        async with _index_warmup_lock:
            lib = await get_library("standard_meta")
            if lib is not None and not is_building(lib.id):
                logger.info("[FastDedup] standard_meta 为空，触发首次构建...")
                try:
                    # 后台跑（可能很久）；本函数只负责轮询等待
                    asyncio.create_task(build_library(lib))
                except Exception as e:
                    logger.error(f"[FastDedup] 触发向量库构建失败：{e}")
                    return False, f"触发向量库构建失败：{e}"

    logger.info("[FastDedup] 向量库构建中，等待完成...")
    deadline = asyncio.get_event_loop().time() + _WAIT_READY_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(5)
        state, _ = await _meta_lib_state()
        if state == "ready":
            return True, ""
        if state in ("stale", "error", "missing"):
            return False, f"向量库构建未成功（状态：{state}）"
    return False, f"等待向量库构建超时（>{_WAIT_READY_TIMEOUT_S}s），请稍后重试"


async def _vector_recall(
        target_no: str,
        target_name: str,
        target_use_range: str,
        pool_id: Optional[int],
        top_k_per_query: int = 25,
        # 阈值换算：旧 FAISS 链路 1/(1+L2²)=0.45 ⇒ L2²≈1.2222 ⇒
        # 归一化向量下 cos = 1−L2²/2 ≈ 0.3889，取 0.39
        sim_threshold: float = 0.39,
) -> List[Dict]:
    """
    用两路向量检索预召回候选（查询模板与 standard_meta 库内容模板逐字符一致）：
      1) 以"标准名称"为查询向量
      2) 以"适用范围"为查询向量（若有）
    合并去重，按 standard_no 取相似度最高的一份。池过滤下推到库的 ref_key IN。
    """
    from app.langchain.tools.db_tools import _get_allowed_standard_nos
    from app.services.vector_hub.search import search_service

    allowed: Optional[set] = None
    if pool_id is not None and pool_id != -1:
        nos = await _get_allowed_standard_nos(pool_id)
        if nos:
            allowed = set(nos)

    fetch_k = top_k_per_query * (5 if allowed else 2)
    ref_in = list(allowed) if allowed else None

    queries: List[str] = []
    if target_name:
        queries.append(f"标准名称：{target_name}\n适用范围：{target_use_range or ''}")
    if target_use_range and target_use_range.strip():
        # 用 use_range 当主查询时，也带上一点名称提示词，避免完全偏离主题
        queries.append(f"标准名称：{target_use_range[:200]}\n适用范围：{target_name or ''}")

    bucket: Dict[str, Dict] = {}
    for q in queries:
        try:
            results = await search_service.search(
                ["standard_meta"], q, top_k=fetch_k, ref_in=ref_in
            )
        except Exception as e:
            logger.error(f"[FastDedup] 向量检索失败 target={target_no}: {e}")
            continue

        for r in results:
            p = r["payload"] or {}
            cand_no = (p.get("standard_no") or "").strip()
            if not cand_no or cand_no.upper() == target_no.strip().upper():
                continue
            similarity = r["score"]
            if similarity < sim_threshold:
                continue
            old = bucket.get(cand_no)
            if old is None or similarity > old["similarity"]:
                bucket[cand_no] = {
                    "standard_no": cand_no,
                    "cname": p.get("cname") or "",
                    "use_range": p.get("use_range") or "",
                    "similarity": round(float(similarity), 4),
                }

    items = sorted(bucket.values(), key=lambda x: x["similarity"], reverse=True)
    items = items[: top_k_per_query * 2]
    logger.info(
        f"[FastDedup] {target_no} 向量预召回 {len(items)} 条（去重合并后）"
    )
    return items


# ── 目标标准基础信息查询 ───────────────────────────────────────────────────────

async def _fetch_target_info(standard_no: str) -> Optional[Dict]:
    import aiomysql
    from app.services.mysql_pool import standard_pool
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT standard_no, cname, use_range "
                    "FROM standard_base_info WHERE standard_no = %s LIMIT 1",
                    [standard_no],
                )
                row = await cur.fetchone()
        return row or None
    except Exception as e:
        logger.error(f"[FastDedup] 查询 {standard_no} 基础信息失败: {e}")
        return None


# ── 提交工具（与原 agent 同形态，闭包累积） ───────────────────────────────────

def _make_submit_tool(
        accumulator: List[SimilarStandardOutput],
        submit_state: Optional[Dict[str, bool]] = None,
):
    @tool
    def submit_candidates(
            candidates_json: Annotated[
                str,
                "候选标准 JSON 数组，每条包含 standard_no/cname/use_range/match_score/llm_score/tags/relation_desc",
            ],
    ) -> str:
        """提交本轮打标完成的候选标准列表。"""
        try:
            items = json.loads(candidates_json)
            if not isinstance(items, list):
                items = [items]
            parsed = [SimilarStandardOutput(**item) for item in items]
            accumulator.extend(parsed)
            # 显式成功提交（含空数组 = “逐条审完、无值得提交的候选”）即视为本轮已完成
            if submit_state is not None:
                submit_state["called"] = True
            return f"已提交 {len(parsed)} 条候选，累计 {len(accumulator)} 条"
        except Exception as e:
            return f"提交失败：{e}。请检查 JSON 格式后重试。"

    return submit_candidates


# ── analyzer agent prompt（标签判定，候选由系统给定） ─────────────────────────

_FAST_ANALYZER_PROMPT = """\
你是一位标准查重分析专家。系统已经通过向量检索为目标标准预召回了一批候选标准，
你**不需要再做任何检索**，只需对给定的候选清单逐条完成标签判定与 llm_score 评分，
最后通过 `submit_candidates(candidates_json)` 一次性提交。

## 标签体系（必须极其严格判定）

### 标准化对象一致（最严格）
核心对象/主题是否完全相同。
- ✅ 针对完全相同的具体对象、产品或技术
- ❌ 通用 vs 专用（应标记为"通用专用关系"）
- ❌ 同领域不同对象（如"螺栓" vs "螺母"）

### 通用专用关系
一个是通用标准，另一个是针对特定领域/产品的专用标准。
**互斥规则：标记为"通用专用关系"时，"标准化对象一致"必须为 false**

### 适用范围重叠
同一主体是否会同时采用两个标准（必须是实际常见情况）。

## 关于同系列标准

同系列标准（同前缀同主号，如 GB/T 11313 与 GB/T 11313.2-2008）由代码规则**已经预先召回并打标**，
**你不需要重复处理**。即便候选清单里偶然出现了同系列条目，请直接**跳过、不要提交**，避免重复并避免覆盖规则已定的标签。

## llm_score 评分

- 1.0 = 高度重复，几乎是同一标准
- 0.8~0.9 = 强相关，存在明显重叠
- 0.6~0.7 = 中等相关，部分交叉
- 0.3~0.5 = 弱相关，仅有边缘关联
- < 0.3 = 几乎无关

**四个标签全部为 false 的候选无需关注，可跳过不提交，也可提交，代码层会自动过滤。**

## submit_candidates 调用规范

每条 JSON 形如：
```json
{
  "standard_no": "...",
  "cname": "...",
  "use_range": "...",
  "match_score": 0.78,
  "llm_score": 0.75,
  "tags": {
    "标准化对象一致": true,
    "通用专用关系": false,
    "适用范围重叠": true
  },
  "relation_desc": "..."
}
```

- `match_score` 直接沿用系统给出的向量相似度，不要再修改
- `relation_desc` 控制在 50 字以内，说明两者关系
- 若清单全部被排除，传入空数组 `[]`
"""


def _build_user_message(
        target_no: str,
        target_name: str,
        target_use_range: str,
        candidates: List[Dict],
) -> str:
    """构造交给 analyzer agent 的输入：目标信息 + 系统预召回候选清单。"""
    cand_lines: List[str] = []
    for i, c in enumerate(candidates, 1):
        cand_lines.append(
            f"{i}. standard_no={c['standard_no']} | "
            f"cname={c['cname']} | "
            f"use_range={(c.get('use_range') or '')[:200]} | "
            f"vector_similarity={c['similarity']}"
        )
    cand_text = "\n".join(cand_lines) if cand_lines else "（无）"

    return (
        f"目标标准：\n"
        f"- 编号：{target_no}\n"
        f"- 名称：{target_name}\n"
        f"- 适用范围：{target_use_range or '（空）'}\n\n"
        f"系统已通过向量检索预召回以下候选标准（共 {len(candidates)} 条），请逐条按规则打标：\n"
        f"{cand_text}\n\n"
        f"请对每条候选完成标签和 llm_score 判定，最终调用 submit_candidates 一次性提交结果。"
        f"`match_score` 字段请直接填回上面给出的 vector_similarity。"
    )


# ── 单标准分析 ─────────────────────────────────────────────────────────────────

_MAX_RETRY = 3


async def _analyze_single_standard_fast(
        standard_no: str,
        semaphore: asyncio.Semaphore,
        pool_id: Optional[int] = None,
) -> Dict:
    from app.langchain.agents.structured_agent import make_subagent_runnable

    async with semaphore:
        target = await _fetch_target_info(standard_no)
        if not target:
            return {
                "standard_no": standard_no,
                "standard_name": None,
                "use_range": None,
                "found": False,
                "need_attention": False,
                "similar_standards": None,
                "general_evaluation": None,
                "suggestion": None,
                "error": None,
            }

        target_name = target.get("cname") or ""
        target_use_range = target.get("use_range") or ""

        # 同系列规则召回（与原 agent 一致，纯代码）
        accumulated: List[SimilarStandardOutput] = list(
            await _recall_same_series_candidates(standard_no, pool_id=pool_id)
        )
        same_series_nos = {
            (c.standard_no or "").strip().upper() for c in accumulated if c.standard_no
        }

        # 系统预召回向量候选；剔除已被同系列规则覆盖的条目
        vec_candidates_raw = await _vector_recall(
            target_no=standard_no,
            target_name=target_name,
            target_use_range=target_use_range,
            pool_id=pool_id,
        )
        vec_candidates = [
            c for c in vec_candidates_raw
            if c["standard_no"].strip().upper() not in same_series_nos
        ]

        # 没有任何向量候选时，直接落定结果（仅同系列）
        if vec_candidates:
            for attempt in range(1, _MAX_RETRY + 1):
                submit_state: Dict[str, bool] = {"called": False}
                submit_tool = _make_submit_tool(accumulated, submit_state)
                runnable = make_subagent_runnable(
                    name="dedup-analyzer-fast",
                    system_prompt=_FAST_ANALYZER_PROMPT,
                    tools=[submit_tool],
                )
                user_msg = _build_user_message(
                    standard_no, target_name, target_use_range, vec_candidates
                )

                try:
                    await runnable.ainvoke(
                        {"messages": [{"role": "user", "content": user_msg}]},
                        config={"configurable": {
                            "thread_id": f"dedup-fast-{standard_no}-{uuid.uuid4().hex[:8]}"
                        }},
                    )
                    # 显式成功提交过（含空数组——提示词规定“全部排除时传 []”）即接受结果；
                    # 只有跑完一轮却从未成功提交（偷懒/JSON 始终不合法）才重试
                    if submit_state["called"] or attempt == _MAX_RETRY:
                        break
                    logger.warning(
                        f"[FastDedup] {standard_no} 第 {attempt}/{_MAX_RETRY} 次未调用提交，重试"
                    )
                except Exception as e:
                    logger.error(
                        f"[FastDedup] {standard_no} 第 {attempt}/{_MAX_RETRY} 次打标失败: {e}"
                    )

        # 后处理（与原 agent 完全一致）
        candidates = _dedup_candidates(accumulated)
        candidates = await _verify_candidates_exist(candidates, pool_id=pool_id, target_no=standard_no)
        candidates = _correct_same_series_tag(standard_no, candidates)
        candidates = _filter_all_false_tags(candidates)
        candidates = _filter_candidates(candidates, threshold=0.6, min_keep=3)
        candidates = _recalc_need_attention(candidates)

        need_attention = any(c.need_attention for c in candidates)

        # 由于跳过了主 agent 的总评/建议生成，这里给出一段轻量摘要
        if candidates:
            top = candidates[0]
            if need_attention:
                evaluation = (
                    f"《{target_name}》存在 {len(candidates)} 条相似标准，"
                    f"其中至少一条在对象与适用范围上均与目标标准交叉，需要关注。"
                )
                suggestion = (
                    f"建议优先核对 {top.standard_no or top.cname} 等标准，"
                    f"评估是否存在重复立项或可整合的情况。"
                )
            else:
                evaluation = (
                    f"《{target_name}》检索到 {len(candidates)} 条相似标准，"
                    f"未发现需要重点关注的重复项。"
                )
                suggestion = "可参考相似标准完善范围与术语，但暂无明显重复。"
        else:
            evaluation = f"未检索到与《{target_name}》具有显著相似度的标准。"
            suggestion = "暂无重复风险，可正常推进。"

        logger.info(
            f"[FastDedup] {standard_no} 完成，"
            f"累计 {len(accumulated)} 条 → 筛选后 {len(candidates)} 条，"
            f"need_attention={need_attention}"
        )

        return {
            "standard_no": standard_no,
            "standard_name": target_name or None,
            "use_range": target_use_range or None,
            "found": True,
            "need_attention": need_attention,
            "similar_standards": [c.model_dump() for c in candidates],
            "general_evaluation": evaluation,
            "suggestion": suggestion,
            "error": None,
        }


# ── 单条任务执行（含落库） ────────────────────────────────────────────────────

async def _execute_one_task_fast(
        name_id: int,
        standard_no: str,
        batch_id: int,
        semaphore: asyncio.Semaphore,
        pool_id: Optional[int],
) -> Dict:
    from app.models.standard import StandardDuplicateName

    try:
        await StandardDuplicateName.filter(id=name_id).update(task_status="running")
    except Exception as e:
        logger.warning(f"[FastDedup] 标记 running 失败 name_id={name_id}: {e}")

    try:
        result = await _analyze_single_standard_fast(standard_no, semaphore, pool_id=pool_id)
    except Exception as e:
        logger.error(f"[FastDedup] 标准 {standard_no} 执行异常: {e}")
        result = {
            "standard_no": standard_no,
            "standard_name": None,
            "use_range": None,
            "found": False,
            "need_attention": False,
            "similar_standards": None,
            "general_evaluation": None,
            "suggestion": None,
            "error": f"执行异常: {e}",
        }

    final_status = "failed" if result.get("error") else "done"
    try:
        await StandardDuplicateName.filter(id=name_id).update(
            standard_name=result.get("standard_name"),
            use_range=result.get("use_range"),
            found=result.get("found", False),
            error=result.get("error"),
            need_attention=result.get("need_attention", False),
            general_evaluation=result.get("general_evaluation"),
            suggestion=result.get("suggestion"),
            similar_standards=result.get("similar_standards"),
            task_status=final_status,
        )
    except Exception as e:
        logger.error(f"[FastDedup] 写回 name_id={name_id} 失败: {e}")

    return result


# ── 批量分析入口 ───────────────────────────────────────────────────────────────

async def run_structured_batch_deduplication(
        standard_nos: List[str],
        pool_id: Optional[int],
        batch_name: Optional[str],
        check_existing: bool = False,
) -> Tuple[Optional[str], Optional[Dict]]:
    """
    嵌入式快速批量查重。

    与 standard_deduplication_agent.run_structured_batch_deduplication
    输入输出完全一致，可作为 drop-in replacement。
    """
    from app.langchain.config import langchain_config
    from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

    if check_existing and len(standard_nos) == 1:
        existing = await StandardDuplicateName.filter(
            standard_no=standard_nos[0],
            batch__pool_id=pool_id,
            found=True,
        ).first()
        if existing:
            logger.info(
                f"[FastDedup] 标准 {standard_nos[0]} 在 pool_id={pool_id} 已有记录，直接返回"
            )
            return None, {"exists": True}

    logger.info(f"[FastDedup] 开始批量查重，共 {len(standard_nos)} 个标准")

    # 并发任务启动前先等系统向量库就绪；不就绪（陈旧/构建失败）整批明确报错，
    # 不让每条任务各自拿到空召回后谎报「未检索到相似标准」。
    ready, reason = await _ensure_vector_index_ready()
    if not ready:
        logger.warning(f"[FastDedup] 向量库未就绪，整批拒绝：{reason}")
        return reason, None

    batch_record = await StandardDuplicateBatch.create(
        batch_name=batch_name,
        pool_id=pool_id,
        total_count=len(standard_nos),
        success_count=0,
        failed_count=0,
        duplicate_count=0,
        status="processing",
        mode="fast",
    )
    batch_id = batch_record.id

    await StandardDuplicateName.bulk_create([
        StandardDuplicateName(
            batch_id=batch_id,
            standard_no=sno,
            found=False,
            need_attention=False,
            task_status="pending",
        )
        for sno in standard_nos
    ])
    name_records = await StandardDuplicateName.filter(batch_id=batch_id).order_by("id").all()

    semaphore = asyncio.Semaphore(langchain_config.LLM_MAX_CONCURRENT)
    tasks = [
        _execute_one_task_fast(
            name_id=rec.id,
            standard_no=rec.standard_no,
            batch_id=batch_id,
            semaphore=semaphore,
            pool_id=pool_id,
        )
        for rec in name_records
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=False)

    await _finalize_batch(batch_id)

    processed = sum(1 for r in raw_results if r.get("found"))
    return None, {
        "batch_id": batch_id,
        "total": len(standard_nos),
        "processed": processed,
        "results": raw_results,
    }


# ── 续跑入口 ──────────────────────────────────────────────────────────────────

async def resume_pending_batch(batch_id: int) -> Tuple[Optional[str], Optional[Dict]]:
    """重新调度某个 processing 批次内剩余 pending/failed 的标准（embedding 版）。"""
    from app.langchain.config import langchain_config
    from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

    batch = await StandardDuplicateBatch.filter(id=batch_id).first()
    if not batch:
        return f"批次 {batch_id} 不存在", None

    pending_items = await StandardDuplicateName.filter(
        batch_id=batch_id,
        task_status__in=["pending", "failed"],
    ).order_by("id").all()

    if not pending_items:
        await _finalize_batch(batch_id)
        return None, {"batch_id": batch_id, "resumed": 0}

    logger.info(f"[FastDedup] 续跑批次 {batch_id}，剩余 {len(pending_items)} 条")

    ready, reason = await _ensure_vector_index_ready()
    if not ready:
        logger.warning(f"[FastDedup] 向量库未就绪，续跑拒绝：{reason}")
        return reason, None

    semaphore = asyncio.Semaphore(langchain_config.LLM_MAX_CONCURRENT)
    tasks = [
        _execute_one_task_fast(
            name_id=rec.id,
            standard_no=rec.standard_no,
            batch_id=batch_id,
            semaphore=semaphore,
            pool_id=batch.pool_id,
        )
        for rec in pending_items
    ]
    await asyncio.gather(*tasks, return_exceptions=False)

    await _finalize_batch(batch_id)
    return None, {"batch_id": batch_id, "resumed": len(pending_items)}


__all__ = ["run_structured_batch_deduplication", "resume_pending_batch"]
