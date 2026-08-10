"""
后台任务调度器（APScheduler 嵌入 FastAPI 进程）

jobs:
  nian_feed_nightly    凌晨 03:17  知识库 feed 排序
  standard_vec_daily   凌晨 03:30  标准向量库增量构建
  fill_image_text      每 12h 一次  表格/公式图片 → MinerU → word 字段回填（00:00/12:00，每轮跑 11.5h）

- 单进程：当前 docker-compose 是单 worker，多 worker 时改用 Redis 锁
- 容错：单条记录失败不影响其他记录
- 节流：Semaphore 控制 MinerU 并发
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Optional
from urllib.parse import quote, unquote

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.settings.config import settings

_scheduler: Optional[AsyncIOScheduler] = None
_BATCH_CONCURRENCY = 5  # 同时跑 5 个用户的 feed agent

# ── 图片回填常量 ─────────────────────────────────────────────────────────────────
_FILL_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL
_FILL_FALLBACK_HOST = "http://dzsy.iyunwen.com"  # 主地址无数据时，用此 host + record.image 字段
_FILL_CONCURRENCY = 3  # MinerU 并发上限（单次解析 10-60s，不宜过高）
_FILL_MAX_SECONDS = int(11.5 * 3600)  # 11.5h 后停止提交新任务，剩余留到下一轮（每 12h 一次）
# MinerU 解析 backend 回退顺序：先用 vlm-engine（快、显存需求高，可能失败），
# 全部重试失败后回退到 pipeline（慢但稳定，纯 CPU/流水线）
_FILL_BACKENDS = ("vlm-engine", "pipeline")


def _is_fill_download_error(exc: Exception) -> bool:
    """判断是否为图片下载阶段的错误（HTTP 4xx/5xx、连接超时等）。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)):
        return True
    return False


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    return _scheduler


async def _run_for_active_users() -> None:
    """
    凌晨为「昨天有登录」的活跃用户跑 feed_ranking_agent。

    守卫：仅 User.last_login >= 昨天 00:00 的用户被纳入候选——僵尸账户不烧 LLM。
    再交叉「最近 14 天有 KBEntry 动静」过滤掉空账户。
    """
    from datetime import datetime, time as dtime, timedelta

    from app.langchain.agents.tasks.kb.feed_ranking_agent import rank_feed_for_user
    from app.models.system.admin import User
    from app.services.seekdb import kb_list

    started = time.time()
    today = date.today()
    yesterday = today - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday, dtime.min)
    cutoff_ms = int((time.time() - 14 * 86400) * 1000)

    # 昨天起有登录的活跃用户
    recent_login_users = await User.filter(
        last_login__gte=yesterday_start,
    ).values_list("id", flat=True)
    active_login_uids: set[int] = {int(uid) for uid in recent_login_users}
    if not active_login_uids:
        logger.info("[NianScheduler] 昨天无人登录，跳过整轮排序")
        return

    try:
        rows = await asyncio.to_thread(kb_list, 50_000, 0)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[NianScheduler] kb_list 失败：{e}")
        return

    user_ids: set[int] = set()
    for r in rows:
        meta = r.get("metadata") or {}
        if meta.get("is_archived"):
            continue
        uid = meta.get("user_id")
        if not uid:
            continue
        uid = int(uid)
        if uid not in active_login_uids:
            continue
        if (meta.get("updated_at") or 0) >= cutoff_ms:
            user_ids.add(uid)

    if not user_ids:
        logger.info(f"[NianScheduler] 昨日登录用户 {len(active_login_uids)} 人，但都没有最近 14 天的 KB 动静，跳过")
        return

    logger.info(f"[NianScheduler] 开始为 {len(user_ids)} 个用户跑 feed 排序…")

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)
    total_written = 0
    total_failed = 0

    async def _one(uid: int):
        nonlocal total_written, total_failed
        async with sem:
            try:
                n = await rank_feed_for_user(user_id=uid, feed_date=today)
                total_written += int(n or 0)
            except Exception as e:  # noqa: BLE001
                total_failed += 1
                logger.exception(f"[NianScheduler] user={uid} 排序失败: {e}")

    await asyncio.gather(*[_one(uid) for uid in user_ids])
    duration = time.time() - started
    logger.info(f"[NianScheduler] 完成：用户 {len(user_ids)}，写入 {total_written} 条，失败 {total_failed}，耗时 {duration:.1f}s")


async def _run_standard_vec_daily() -> None:
    """每日 03:30 增量构建标准向量库（meta + chapter）。

    本地 Qwen3-Embedding-8B 服务不可用 / 维度漂移等异常都被吞掉，仅记日志，
    不阻塞同一 scheduler 上的其他任务（如 nian_feed_nightly）。
    """
    from app.services.standard_vec.builder import StandardVecBuilder

    started = time.time()
    try:
        builder = StandardVecBuilder()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[StandardVecScheduler] 初始化 builder 失败：{e}")
        return

    try:
        meta_stat = await builder.build_meta()
        logger.info(f"[StandardVecScheduler] meta 完成：{meta_stat}")
    except Exception:
        logger.exception("[StandardVecScheduler] build_meta 异常（已忽略）")

    try:
        chapter_stat = await builder.build_chapter()
        logger.info(f"[StandardVecScheduler] chapter 完成：{chapter_stat}")
    except Exception:
        logger.exception("[StandardVecScheduler] build_chapter 异常（已忽略）")

    logger.info(f"[StandardVecScheduler] 全部完成，耗时 {time.time() - started:.1f}s")


async def _run_fill_image_text_daily() -> None:
    """
    每 12h 触发一次（00:00 / 12:00）：将 standard_jgh_pdf_table / standard_jgh_pdf_formula 中
    word 为 NULL 或空字符串的记录，通过 MinerU 解析 file_name 对应图片，将结果写回 word。

    - 时间上限 11.5h：时间到后不再提交新任务，剩余记录下一轮（12h 后）继续
    - 频繁部署导致进程重启，任务会在下一个整点自动续跑，不会丢失进度
    - 失败的记录保持 word 为空，下一轮自动重试
    - 环境变量 FILL_IMAGE_TEXT_ENABLED=false 可临时关闭
    """
    from tortoise.expressions import Q
    from app.models.standard.jgh_pdf import StandardJghPdfTable, StandardJghPdfFormula
    from app.utils.mineru import convert_to_markdown, MinerUError

    from datetime import date as _date
    from app.models.standard.image_fill_log import StandardImageFillLog

    started = time.time()
    run_date = _date.today()
    logger.info(f"[FillImageText] 本轮开始，run_date={run_date}")

    # ── 1. 查询待处理记录 ──────────────────────────────────────────────────────────
    need_word = Q(word__isnull=True) | Q(word="")

    pending_table = await StandardJghPdfTable.filter(need_word, file_name__isnull=False).exclude(file_name="").values_list("id", "file_name", "image")

    pending_formula = await StandardJghPdfFormula.filter(need_word, file_name__isnull=False).exclude(file_name="").values_list("id", "file_name", "image")

    queue: list[tuple] = [(StandardJghPdfTable, id_, fn, img) for id_, fn, img in pending_table] + [(StandardJghPdfFormula, id_, fn, img) for id_, fn, img in pending_formula]

    total = len(queue)
    if total == 0:
        logger.info("[FillImageText] 无待处理记录，本轮跳过")
        return

    logger.info(f"[FillImageText] 待处理：表格 {len(pending_table)} 条，公式 {len(pending_formula)} 条，共 {total} 条")

    # ── 2. 时间看守 + 计数器 ────────────────────────────────────────────────────────
    ok_count = 0
    fail_count = 0
    stop_event = asyncio.Event()

    async def _time_watcher() -> None:
        await asyncio.sleep(_FILL_MAX_SECONDS)
        stop_event.set()
        logger.info(f"[FillImageText] 达到时间上限 {_FILL_MAX_SECONDS / 3600:.1f}h，后续任务将跳过，剩余记录下一轮（12h 后）继续")

    watcher = asyncio.create_task(_time_watcher())

    # ── 3. Queue + 固定 worker 并发处理 ──────────────────────────────────────────
    # 用 Queue 而非 gather(*所有协程)：避免数万条记录时一次性创建数万个协程对象
    q: asyncio.Queue[tuple | None] = asyncio.Queue()
    for item in queue:
        await q.put(item)

    async def _write_log(
        source_table: str,
        source_id: int,
        file_name: str,
        status: str,
        elapsed_ms: int,
        error_msg: str | None = None,
    ) -> None:
        try:
            await StandardImageFillLog.create(
                run_date=run_date,
                source_table=source_table,
                source_id=source_id,
                file_name=file_name,
                status=status,
                error_msg=error_msg,
                elapsed_ms=elapsed_ms,
            )
        except Exception as log_err:
            logger.warning(f"[FillImageText] 日志写入失败 id={source_id}: {log_err}")

    _FILL_MAX_RETRIES = 3
    _FILL_RETRY_DELAY = 5  # seconds

    async def _worker() -> None:
        nonlocal ok_count, fail_count
        while True:
            item = await q.get()
            try:
                if item is None:  # 毒丸：退出信号
                    return
                if stop_event.is_set():
                    return  # 剩余记录不计入日志，由汇总统计 not_done
                model_cls, record_id, file_name, image_path = item
                table_name: str = model_cls.Meta.table
                # DB 里的 file_name 混杂两种形态：既有真实空格，也有已编码好的 %20。
                # 直接 quote() 会把已编码的 % 再编码成 %25（%20→%2520）导致 404；
                # 先 unquote() 还原成原始字面值，再 quote() 统一编码，两种来源都归一为正确形态。
                primary_url = _FILL_IMAGE_BASE_URL + quote(unquote(file_name.lstrip("/")), safe="/")
                fallback_url = (_FILL_FALLBACK_HOST + quote(unquote(image_path), safe="/")) if image_path else None
                url = primary_url
                t0 = time.monotonic()
                last_error = ""
                success = False
                aborted = False  # 非 MinerU 异常（如网络/DB）视为硬失败，不再回退其它 backend
                used_fallback = False
                # backend 回退：vlm-engine 全部重试失败后，用 pipeline 再解析一次
                for bi, backend in enumerate(_FILL_BACKENDS):
                    for attempt in range(1, _FILL_MAX_RETRIES + 1):
                        try:
                            markdown = await convert_to_markdown(url, backend=backend)
                            elapsed_ms = int((time.monotonic() - t0) * 1000)
                            await model_cls.filter(id=record_id).update(word=markdown)
                            ok_count += 1
                            await _write_log(table_name, record_id, file_name, "ok", elapsed_ms)
                            success = True
                            break
                        except MinerUError as e:
                            last_error = str(e)
                            # 图片下载失败（响应内容为空等）→ 切换到 fallback URL
                            if "图片下载失败" in str(e) and not used_fallback and fallback_url:
                                logger.info(f"[FillImageText] 图片下载失败（内容为空），切换到备用地址：{fallback_url}  table={table_name} id={record_id}")
                                url = fallback_url
                                used_fallback = True
                                continue
                            if attempt < _FILL_MAX_RETRIES:
                                logger.warning(
                                    f"[FillImageText] MinerU 失败  table={table_name} id={record_id} backend={backend} (attempt {attempt}/{_FILL_MAX_RETRIES}): {e}，{_FILL_RETRY_DELAY}s 后重试"
                                )
                                await asyncio.sleep(_FILL_RETRY_DELAY)
                            else:
                                logger.warning(f"[FillImageText] MinerU 失败  table={table_name} id={record_id} backend={backend} (all {_FILL_MAX_RETRIES} attempts exhausted): {e}")
                        except Exception as e:
                            last_error = str(e)
                            # httpx 下载类错误（4xx/5xx/超时等）→ 触发 fallback
                            if not used_fallback and fallback_url and _is_fill_download_error(e):
                                logger.info(f"[FillImageText] 图片下载失败，切换到备用地址：{fallback_url}  table={table_name} id={record_id}")
                                url = fallback_url
                                used_fallback = True
                                continue
                            aborted = True
                            logger.warning(f"[FillImageText] 处理异常  table={table_name} id={record_id} backend={backend} file={file_name}: {e}")
                            break
                    if success or aborted:
                        break
                    # 当前 backend 全部重试失败，若还有下一个 backend 则回退继续
                    if bi < len(_FILL_BACKENDS) - 1:
                        logger.warning(f"[FillImageText] backend={backend} 全部重试失败，回退到 backend={_FILL_BACKENDS[bi + 1]} 重新解析  table={table_name} id={record_id}")

                if not success:
                    fail_count += 1
                    await _write_log(
                        table_name,
                        record_id,
                        file_name,
                        "failed",
                        int((time.monotonic() - t0) * 1000),
                        last_error,
                    )
            finally:
                q.task_done()

    # 投入毒丸让每个 worker 退出
    for _ in range(_FILL_CONCURRENCY):
        await q.put(None)

    try:
        await asyncio.gather(*[_worker() for _ in range(_FILL_CONCURRENCY)])
    finally:
        watcher.cancel()  # 无论正常结束还是被取消，都清理 watcher

    # ── 4. 汇总日志 ───────────────────────────────────────────────────────────────
    elapsed = time.time() - started
    not_done = total - ok_count - fail_count  # 因时间上限未处理的记录，12h 后续跑
    stop_reason = f"时间上限 {_FILL_MAX_SECONDS / 3600:.1f}h" if stop_event.is_set() else "全部完成"
    logger.info(f"[FillImageText] 本轮结束（{stop_reason}）：成功 {ok_count}，失败 {fail_count}，未处理 {not_done}，耗时 {elapsed / 3600:.2f}h")


def start() -> None:
    """启动调度器（在 FastAPI lifespan 里调）。"""
    sched = get_scheduler()
    if sched.running:
        return

    # 凌晨 03:17 跑（避开整点）
    sched.add_job(
        _run_for_active_users,
        trigger=CronTrigger(hour=3, minute=17),
        id="nian_feed_nightly",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,  # 服务停了又启动时，1h 内的 misfire 仍补跑
    )

    import os

    # 凌晨 03:30 跑标准向量库增量构建（与 nian_feed 错开）
    # STANDARD_VEC_DAILY_ENABLED=false 时关闭——首次全量构建期建议关闭，跑完再打开
    if (os.getenv("STANDARD_VEC_DAILY_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on"):
        sched.add_job(
            _run_standard_vec_daily,
            trigger=CronTrigger(hour=3, minute=30),
            id="standard_vec_daily",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info("[Scheduler] standard_vec_daily 已启用（每日 03:30）")
    else:
        logger.info("[Scheduler] standard_vec_daily 已禁用（STANDARD_VEC_DAILY_ENABLED=false）")

    # 每 12 小时触发一次（00:00 / 12:00），每轮最多跑 11.5h 后停止提交新任务
    # 频繁部署导致进程重启时可自动在下一个整点续跑剩余记录
    if (os.getenv("FILL_IMAGE_TEXT_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on"):
        sched.add_job(
            _run_fill_image_text_daily,
            trigger=CronTrigger(hour="0,12", minute=0),
            id="fill_image_text_12h",
            replace_existing=True,
            coalesce=True,
            max_instances=1,  # 防止上一轮未跑完时重叠触发
            misfire_grace_time=3600,
        )
        logger.info("[Scheduler] fill_image_text_12h 已启用（每 12h：00:00 / 12:00）")
    else:
        logger.info("[Scheduler] fill_image_text_12h 已禁用（FILL_IMAGE_TEXT_ENABLED=false）")

    sched.start()
    logger.info("[Scheduler] 已启动 (Asia/Shanghai)")


async def shutdown() -> None:
    """关闭调度器（在 FastAPI lifespan 里调）。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[NianScheduler] 已关闭")
    _scheduler = None


# ── Agent 定时任务调度 ───────────────────────────────────────────────────────────


def register_scheduled_task(task) -> None:
    """将一个 AgentScheduledTask 注册到 APScheduler。

    Parameters
    ----------
    task : AgentScheduledTask
        必须是 status='active' 的任务。
    """
    from apscheduler.triggers.cron import CronTrigger as _CT

    sched = get_scheduler()
    sched.add_job(
        _run_scheduled_task,
        trigger=_CT.from_crontab(task.cron_expr),
        id=f"scheduled_task_{task.id}",
        args=[task.id],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    logger.info(f"[ScheduledTask] 注册: id={task.id} key={task.task_key} cron={task.cron_expr}")


def unregister_scheduled_task(task) -> None:
    """从 APScheduler 移除定时任务。"""
    sched = get_scheduler()
    job_id = f"scheduled_task_{task.id}"
    try:
        sched.remove_job(job_id)
        logger.info(f"[ScheduledTask] 移除: id={task.id} key={task.task_key}")
    except Exception:
        pass  # job 可能已不存在


async def _run_scheduled_task(task_id: int) -> None:
    """APScheduler 回调：为定时任务创建 session → 跑一轮 Agent → 保存结果。

    参考 qa.py 的 daily_brief 模式：创建 agent → 发送 prompt → 收集输出。

    多实例去重：多个服务连同一个 DB 时，各自都会恢复注册同一批任务、几乎同时触发。
    入口处用原子 UPDATE 抢「分钟槽位」执行权（cron 最小粒度即分钟），
    只有抢到（affected_rows == 1）的实例真正执行，其余实例直接跳过。
    """
    import secrets
    from datetime import datetime, timezone

    from tortoise.expressions import Q

    from app.models.standard.agent_task import AgentScheduledTask, AgentScheduledTaskRun
    from app.models.standard.agent import AgentSession, AgentMessage

    task = await AgentScheduledTask.get_or_none(id=task_id, is_deleted=0)
    if not task or task.status != "active":
        logger.warning(f"[ScheduledTask] 任务不存在或已停用: id={task_id}")
        return

    # 抢执行权：last_fire_slot 为空、或属于更早的槽位时才能抢占成功；
    # 同一分钟槽内只有第一个 UPDATE 成功的实例得到执行权。
    # 槽位必须用 UTC 计算：若用本地时间，两个实例容器时区不一致（如一个 UTC 一个东八区）
    # 会导致同一时刻算出不同槽位，两边都抢到 → 退回重复执行
    slot = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    affected = await AgentScheduledTask.filter(
        Q(last_fire_slot__isnull=True) | ~Q(last_fire_slot=slot),
        id=task_id,
    ).update(last_fire_slot=slot)
    if affected != 1:
        logger.info(f"[ScheduledTask] 本次触发（slot={slot}）已由其他实例执行，跳过: id={task_id} key={task.task_key}")
        return

    logger.info(f"[ScheduledTask] 开始执行: id={task_id} key={task.task_key} title={task.title}")
    t0 = time.monotonic()

    # ── 组装执行上下文：让执行 agent 知道本次与上次执行的时间关系 ──────────────────
    # 采集类任务（如"每天采集新闻"）需要的是「上次执行 → 本次执行」之间的增量内容，
    # 因此把本次时间、上次成功执行时间、上次结果摘要一并注入触发消息。
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(task.timezone or "Asia/Shanghai")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(timezone.utc).astimezone(tz)

    last_local = None
    if task.last_run_at is not None:
        last_local = task.last_run_at
        if last_local.tzinfo is None:  # Tortoise use_tz=False 时读回为 naive（实际是 UTC）
            last_local = last_local.replace(tzinfo=timezone.utc)
        last_local = last_local.astimezone(tz)

    ctx_lines = [
        "【定时任务自动触发】",
        f"本次执行时间：{now_local:%Y-%m-%d %H:%M}",
        f"上次成功执行时间：{last_local:%Y-%m-%d %H:%M}" if last_local else "上次成功执行时间：无（这是首次执行）",
    ]
    last_done_run = await AgentScheduledTaskRun.filter(task_id=task.id, status="done").order_by("-create_time").first()
    if last_done_run and last_done_run.result_summary:
        ctx_lines.append(f"上次执行结果摘要：{last_done_run.result_summary[:200]}")
    ctx_lines.append("若任务涉及内容采集/汇总，默认只处理「上次成功执行时间」到「本次执行时间」之间的新增内容，除非任务指令中明确要求其它范围。")

    fire_prompt = f"{task.prompt}\n\n---\n" + "\n".join(ctx_lines)

    # 创建专属会话
    session_key = f"sess_{secrets.token_hex(6)}"
    thread_id = f"stask-{task.task_key}-{int(time.time())}"
    session = await AgentSession.create(
        session_key=session_key,
        user_id=task.user_id,
        title=f"[定时] {task.title}",
        thread_id=thread_id,
    )

    # 保存用户消息（含执行上下文的完整触发内容，用户在会话里可看到）
    user_msg = await AgentMessage.create(
        session_id=session.id,
        role="user",
        content=fire_prompt,
        status="done",
    )
    # assistant 占位
    assistant_msg = await AgentMessage.create(
        session_id=session.id,
        role="assistant",
        status="streaming",
    )

    try:
        # 构建 agent（复用 qa.py 的基础设施）
        from pathlib import Path
        from app.api.v1.ai.qa import _user_workspace, _session_tmp_dir, _get_shared_resources
        from app.langchain.agents.qa_agent import create_qa_agent
        from app.services.agent_runtime.call_context import (
            AgentCallContext,
            set_agent_call_context,
            clear_agent_call_context,
        )

        ws = _user_workspace(task.user_id)
        _session_tmp_dir(ws, session_key)  # 确保 session 临时目录存在
        shared = await _get_shared_resources()

        # 按角色模型配置：定时任务代表用户执行，同样守角色配置。解析 + set 请求级
        # CTX（CTX_PROFILE / CTX_GEN_BLOCK_OVERRIDE 随 to_thread 拷贝进构建线程，
        # 生成能力门卫与 chat 块构建自动跟随）。is_super 不传，定时任务不扩权。
        from app.langchain.role_model_profile import apply_gen_override, resolve_user_model_profile

        _profile = await resolve_user_model_profile(task.user_id)
        apply_gen_override(_profile)

        def _build():
            return create_qa_agent(
                extra_tools=list(shared["mcp_tools"]),
                store=shared["store"],
                root_dir=str(ws),
                user_id=task.user_id,
                chat_block_key=_profile.chat_block_key,
            )

        agent = await asyncio.to_thread(_build)

        config = {"configurable": {"thread_id": thread_id, "user_id": str(task.user_id)}}

        # 设置 Agent 调用上下文（与正常 QA 聊天一致）
        set_agent_call_context(
            AgentCallContext(
                session_id=session.id,
                session_key=session_key,
                message_id=assistant_msg.id,
                workspace_dir=ws,
            )
        )

        # 计费上下文
        from app.core.ctx import CTX_BILLING_BIZ_ENTRY, CTX_BILLING_SESSION_ID

        CTX_BILLING_BIZ_ENTRY.set("scheduled-task")
        CTX_BILLING_SESSION_ID.set(session.id)

        # 执行 agent，收集输出（无 SSE，直接收集）
        final_content = ""
        collected_chunks: list[str] = []
        async for namespace, stream_mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": fire_prompt}]},
            config=config,
            stream_mode=["messages", "updates"],
            subgraphs=True,
        ):
            is_subagent = bool(namespace)
            if stream_mode == "messages":
                token, metadata = chunk
                node = metadata.get("langgraph_node", "")
                content = getattr(token, "content", "") or ""
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                if not is_subagent and node == "model" and content:
                    collected_chunks.append(content)
            elif stream_mode == "updates" and not is_subagent:
                for node, node_update in chunk.items():
                    if node != "model" or not node_update:
                        continue
                    for msg in node_update.get("messages") or []:
                        msg_content = getattr(msg, "content", "") or ""
                        if isinstance(msg_content, list):
                            msg_content = " ".join(c.get("text", "") for c in msg_content if isinstance(c, dict))
                        if msg_content.strip() and not (getattr(msg, "tool_calls", None) or []):
                            final_content = msg_content.strip()

        result = final_content or "".join(collected_chunks)
        duration_ms = int((time.monotonic() - t0) * 1000)

        # 保存 assistant 消息
        assistant_msg.content = result
        assistant_msg.status = "done"
        await assistant_msg.save()

        # 更新任务记录（指定 update_fields：避免把内存中的旧 last_fire_slot 回写覆盖抢占值）
        task.last_run_at = datetime.now(timezone.utc)
        task.last_session_key = session_key
        task.run_count += 1
        await task.save(update_fields=["last_run_at", "last_session_key", "run_count"])

        # 写入执行记录
        await AgentScheduledTaskRun.create(
            task_id=task.id,
            user_id=task.user_id,
            session_key=session_key,
            status="done",
            result_summary=result[:500] if result else None,
            duration_ms=duration_ms,
        )

        # 更新会话消息数
        session.message_count = 2
        await session.save()

        logger.info(f"[ScheduledTask] 完成: id={task_id} key={task.task_key} session={session_key} result_len={len(result)} duration={duration_ms}ms")

    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(f"[ScheduledTask] 执行失败: id={task_id} key={task.task_key}: {e}")
        assistant_msg.content = None
        assistant_msg.status = "error"
        assistant_msg.error = str(e)
        await assistant_msg.save()

        # 写入失败记录
        await AgentScheduledTaskRun.create(
            task_id=task.id,
            user_id=task.user_id,
            session_key=session_key,
            status="error",
            error=str(e)[:500],
            duration_ms=duration_ms,
        )
    finally:
        clear_agent_call_context()


async def restore_scheduled_tasks() -> None:
    """启动恢复：将 DB 中 active 的定时任务重新注册到 APScheduler。"""
    from app.models.standard.agent_task import AgentScheduledTask

    tasks = await AgentScheduledTask.filter(status="active", is_deleted=0)
    if not tasks:
        logger.info("[ScheduledTask] 无需恢复（无 active 任务）")
        return

    for task in tasks:
        register_scheduled_task(task)
    logger.info(f"[ScheduledTask] 恢复注册 {len(tasks)} 个定时任务")
