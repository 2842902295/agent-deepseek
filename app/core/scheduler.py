"""
后台任务调度器（APScheduler 嵌入 FastAPI 进程）

jobs:
  std_data_daily       凌晨 02:30  标准库指纹增量同步（源库 → 本地业务库）
  nian_feed_nightly    凌晨 03:17  知识库 feed 排序
  standard_vec_daily   凌晨 03:30  标准向量库增量构建
  fill_image_text      每月 1 号 04:20  表格/公式图片 → MinerU → word 字段回填（每轮最长跑 96h；启动时按需补跑）

- 标准库 / 向量库维护类任务（std_data_daily / standard_vec_daily / fill_image_text）
  仅 BRAND_VARIANT=standard（行业版）注册；generic（通用版）无标准库数据源，不做维护
- 单进程：当前 docker-compose 是单 worker，多 worker 时改用 Redis 锁
- 容错：单条记录失败不影响其他记录
- 节流：Semaphore 控制 MinerU 并发
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from app.settings.config import settings

_scheduler: Optional[AsyncIOScheduler] = None
_BATCH_CONCURRENCY = 5  # 同时跑 5 个用户的 feed agent

# ── 图片回填常量 ─────────────────────────────────────────────────────────────────
# 图片地址候选前缀（优先级从高到低，解析与拼接统一在 app/utils/image_ref.py）：
#   内网网关 JGH_IMAGE_BASE_URL → image 里 <img src> 的绝对地址 → 公网镜像 → 旧站相对路径
_FILL_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL
_FILL_IMAGE_PUBLIC_BASE_URL = settings.JGH_IMAGE_PUBLIC_BASE_URL
_FILL_IMAGE_LEGACY_HOST = settings.JGH_IMAGE_LEGACY_HOST
_FILL_CONCURRENCY = 3  # MinerU 并发上限（单次解析 10-60s，不宜过高——打爆 MinerU 比慢更糟）
# 单轮时间上限 96h（4 天）：每月只跑一次，跑不完的记录要等下个月，所以给足窗口。
# 按 3 并发、单张 10~60s 估算，96h 约可处理 2.5~3 万张
_FILL_MAX_SECONDS = int(96 * 3600)
# MinerU 解析 backend 回退顺序：先用 vlm-engine（快、显存需求高，可能失败），
# 全部重试失败后回退到 pipeline（慢但稳定，纯 CPU/流水线）
_FILL_BACKENDS = ("vlm-engine", "pipeline")
_FILL_MAX_RETRIES = 5  # 每个 backend 的重试次数（月度轮次机会稀缺，多给两次）
_FILL_RETRY_DELAY = 5  # seconds
_FILL_DOWNLOAD_TIMEOUT = 60.0  # 单张图片下载超时（秒）
# 主图片服务连续这么多次 5xx/连不上 → 本轮判定其不可用，后续记录直接跳过该候选
# （否则一轮要对着挂掉的网关白打数万次请求）。月度轮次跨 4 天，抖动概率更高，阈值放宽
_FILL_PRIMARY_DOWN_THRESHOLD = 10
# MinerU 服务级失败（5xx / 连不上 / 对端断开 / 超时，不含"这张图解析不出来"的 MinerUError）
# 连续这么多次 → 判定 MinerU 挂了，本轮提前结束。同上：4 天的长轮次不能因短时抖动整轮放弃
_FILL_MINERU_DOWN_THRESHOLD = 20
_FILL_PROGRESS_EVERY = 200  # 每处理这么多条打一行进度（长轮次下 50 条一行会刷出上万行）
# 熔断（status='dead'）自动过期天数：超过这么久没再尝试过的 dead 记录，本轮开始前解除，
# 给一次重试机会（图片可能是当时还没同步到公网镜像）。0 表示永不自动解除
_FILL_DEAD_TTL_DAYS = 90
# 启动补跑判据：fill_log 最新一行距今超过这么多天（或表为空）→ 进程启动后补跑一轮。
# 覆盖「每月触发时刻服务正好停机/频繁部署错过窗口」的情况
_FILL_CATCHUP_AFTER_DAYS = 30
_FILL_CATCHUP_DELAY_MINUTES = 3  # 启动后延迟几分钟再补跑，避开启动高峰（DB 迁移、模型播种）

# fill_log.status 取值。dead = 所有候选地址都明确 HTTP 4xx（图确实不存在），后续轮次不再重试
# （超过 _FILL_DEAD_TTL_DAYS 自动解除）；立即解除：DELETE FROM standard_image_fill_log WHERE status='dead'
_STATUS_OK = "ok"
_STATUS_FAILED = "failed"
_STATUS_DEAD = "dead"

# 防止启动补跑与月度定时轮次重叠（两个 job id 各自 max_instances=1 管不住跨 job）
_fill_running = False


async def _mineru_reachable() -> tuple[bool, str]:
    """MinerU Gradio 服务探活。返回 (是否可用, 详情)。

    只有 5xx / 连不上算不可用；4xx 说明服务在跑（路径不对而已），仍视为可用。
    """
    url = f"{settings.MINERU_BASE_URL}/"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 500:
            return False, f"HTTP {resp.status_code}"
        return True, f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    return _scheduler


def _is_standard_brand() -> bool:
    """品牌变体是否为 standard（行业版）。

    标准库 / 向量库维护类任务（std_data_daily / standard_vec_daily / fill_image_text）
    仅 standard 变体需要：generic（通用版）部署没有标准库数据源，不做任何维护。
    """
    return (settings.BRAND_VARIANT or "standard").lower() == "standard"


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


async def _run_std_data_daily() -> None:
    """每日 02:30 标准库指纹增量同步（源库 → 本地业务库，见 app/services/std_sync.py）。

    排在 03:30 向量库同步之前，新增章节当天即可入库向量。
    引擎自带并发守卫与异常落库，这里只做触发；已在跑（如手工触发未结束）则跳过。
    """
    from app.services import std_sync

    if std_sync.is_running():
        logger.info("[StdSyncScheduler] 已有同步任务在运行，本轮跳过")
        return
    try:
        await std_sync.run_sync(trigger="cron")
    except Exception:
        logger.exception("[StdSyncScheduler] 定时同步异常（已忽略）")


async def _run_standard_vec_daily() -> None:
    """每日 03:30 增量同步全部系统向量库（standard_meta / standard_chapter / standard_term）。

    走 vector_hub 三档增量（diff 下推 SQL，只重嵌内容变化行）。
    陈旧库（切过 embed 块）不会被自动重建——重建是全量重嵌的费用事件，必须显式触发：
    build_library 的陈旧守卫会拒绝不带 wipe 的构建（抛异常），库保持陈旧状态
    （前端展示陈旧横幅 + 重建按钮），等超管在向量库管理面板显式重建。
    写入链路系统性失败（如向量索引内存不足）由摄入层熔断中止并落 error 状态，
    不会像过去那样把整库嵌入额度白烧完还报"成功"。
    任何异常都被吞掉，仅记日志，不阻塞同一 scheduler 上的其他任务。
    """
    from app.services.vector_hub.build import build_library
    from app.services.vector_hub.library_service import SYSTEM_LIBRARIES, get_library

    started = time.time()
    for key in [seed["library_key"] for seed in SYSTEM_LIBRARIES]:
        try:
            lib = await get_library(key)
            if lib is None:
                logger.warning(f"[VecHubScheduler] 系统库 {key} 不存在，跳过")
                continue
            stat = await build_library(lib)
            logger.info(f"[VecHubScheduler] {key} 完成：{stat}")
        except Exception:
            logger.exception(f"[VecHubScheduler] {key} 同步异常（已忽略）")

    logger.info(f"[VecHubScheduler] 全部完成，耗时 {time.time() - started:.1f}s")


async def _fill_image_text_round() -> None:
    """
    图片文本回填的一轮完整执行体（触发入口见 `_run_fill_image_text_monthly` / `_run_fill_image_text_catchup`）：
    将 standard_jgh_pdf_table / standard_jgh_pdf_formula 中 word 为 NULL 或空字符串的记录，
    通过 MinerU 解析对应图片，将结果写回 word。

    单条流程分两段（下载与解析解耦，各自的失败各自处理）：

    1. **下载**：`image_ref.build_image_candidates` 产出候选地址，逐个尝试直到拿到字节。顺序 =
       内网网关（JGH_IMAGE_BASE_URL + file_name）→ image 里 `<img src>` 的绝对地址（上游公网原址，
       路径前缀可能是 admin-api 或 admin-api-factory，必须原样用）→ 公网镜像（+ file_name）→
       旧站（JGH_IMAGE_LEGACY_HOST + 相对路径）。
       ⚠️ image 字段混存 HTML `<img>` 标签与站内相对路径，**绝不能直接和 host 字符串相加**——
       旧实现拼出 `http://dzsy.iyunwen.com%3Cimg%20src%3D...` 这种 host 里带 HTML 的畸形 URL，
       DNS/协议直接报错，每轮数万条白跑、日志刷屏，还把真正可下载的公网地址埋没了。
    2. **解析**：MinerU backend 回退（vlm-engine → pipeline），每个 backend 重试 _FILL_MAX_RETRIES 次。

    熔断：所有候选都返回 HTTP 4xx（图确实不存在）→ fill_log 记 status='dead'，后续轮次跳过；
    5xx / 超时 / 连不上 / 对端断开等临时故障**不**熔断，下一轮继续。
    dead 超过 _FILL_DEAD_TTL_DAYS 天自动解除（月度轮次机会少，别把当时没同步好的图永久拉黑）；
    立即解除：`DELETE FROM standard_image_fill_log WHERE status='dead'`。

    其它：

    - **MinerU 探活**：轮前先探一次 MinerU 服务，不可用直接跳过本轮（否则一整轮只会白下载
      数万张图再刷失败日志）；轮中若连续 _FILL_MINERU_DOWN_THRESHOLD 次服务级失败
      （5xx/连不上/对端断开，不含"这张图解析不出来"），判定其挂掉并提前结束本轮
    - 时间上限 _FILL_MAX_SECONDS（96h）：时间到后不再提交新任务，剩余记录留到下一轮（下个月）
    - 主图片服务本轮连续 5xx/连不上 _FILL_PRIMARY_DOWN_THRESHOLD 次 → 判定不可用，
      后续记录直接跳过该候选（不再对着挂掉的网关白打数万次请求），轮末告警提示排查
    - 单条错误按类型聚合计数，同类型只在首次出现时 WARNING 一次，轮末打一张分类汇总（防刷屏）
    - 环境变量 FILL_IMAGE_TEXT_ENABLED=false 可临时关闭
    """
    from tortoise.expressions import Q

    from app.models.standard.image_fill_log import StandardImageFillLog
    from app.models.standard.jgh_pdf import StandardJghPdfFormula, StandardJghPdfTable
    from app.utils.image_ref import FetchedImage, ImageFetchError, build_image_candidates, fetch_first_available
    from app.utils.mineru import MinerUError, convert_bytes_to_markdown

    started = time.time()
    run_date = date.today()
    logger.info(f"[FillImageText] 本轮开始，run_date={run_date}")

    # ── 0. MinerU 探活：服务不可用就整轮跳过 ────────────────────────────────────────
    alive, alive_detail = await _mineru_reachable()
    if not alive:
        logger.error(f"[FillImageText] MinerU 服务 {settings.MINERU_BASE_URL} 不可用（{alive_detail}），本轮跳过；请排查该服务，下轮（下月 1 号或重启补跑）自动重试")
        return
    logger.info(f"[FillImageText] MinerU 探活通过（{alive_detail}）")

    # ── 1. 查询待处理记录 ──────────────────────────────────────────────────────────
    # file_name 为空的行只剩相对路径/占位图（spacer.gif）或空值，无解析价值，仍按原口径排除
    need_word = Q(word__isnull=True) | Q(word="")

    pending_table = await StandardJghPdfTable.filter(need_word, file_name__isnull=False).exclude(file_name="").values_list("id", "file_name", "image")

    pending_formula = await StandardJghPdfFormula.filter(need_word, file_name__isnull=False).exclude(file_name="").values_list("id", "file_name", "image")

    raw_queue: list[tuple] = [(StandardJghPdfTable, id_, fn, img) for id_, fn, img in pending_table] + [(StandardJghPdfFormula, id_, fn, img) for id_, fn, img in pending_formula]

    # 熔断过期清理：太久没再试过的 dead 记录解除熔断，本轮重新给一次机会
    if _FILL_DEAD_TTL_DAYS > 0:
        ttl_before = datetime.now(timezone.utc) - timedelta(days=_FILL_DEAD_TTL_DAYS)
        try:
            expired = await StandardImageFillLog.filter(status=_STATUS_DEAD, create_time__lt=ttl_before).delete()
            if expired:
                logger.info(f"[FillImageText] 解除过期熔断 {expired} 条（>{_FILL_DEAD_TTL_DAYS} 天未重试）")
        except Exception as e:  # noqa: BLE001 —— 清理失败不影响本轮主流程
            logger.warning(f"[FillImageText] 过期熔断清理失败（忽略）：{e}")

    # 已熔断（历轮判定图片确定不存在）的记录本轮不再重试
    dead_rows = await StandardImageFillLog.filter(status=_STATUS_DEAD).values_list("source_table", "source_id")
    dead_set = {(str(t), int(i)) for t, i in dead_rows}
    queue: list[tuple] = [it for it in raw_queue if (str(it[0].Meta.table), int(it[1])) not in dead_set]
    skipped_dead = len(raw_queue) - len(queue)

    total = len(queue)
    if total == 0:
        logger.info(f"[FillImageText] 无待处理记录，本轮跳过（已熔断 {skipped_dead} 条）")
        return

    n_table = sum(1 for it in queue if it[0] is StandardJghPdfTable)
    logger.info(f"[FillImageText] 待处理：表格 {n_table} 条，公式 {total - n_table} 条；已熔断跳过 {skipped_dead} 条")

    # ── 2. 时间看守 + 计数器 ────────────────────────────────────────────────────────
    ok_count = 0
    fail_count = 0
    dead_count = 0
    processed = 0
    ok_via_backup = 0  # 靠非首选候选地址成功的条数（反映主地址健康度）
    err_kinds: Counter[str] = Counter()
    seen_errors: set[str] = set()
    # 主图片服务健康度（本轮内所有 worker 共享；asyncio 单线程，无需加锁）
    primary_prefix = (_FILL_IMAGE_BASE_URL or "").strip().rstrip("/")
    primary_state: dict[str, object] = {"fails": 0, "down": False}
    # MinerU 服务健康度：连续服务级失败达阈值即提前结束本轮（成功一次即清零）
    mineru_state: dict[str, object] = {"fails": 0, "down": False}
    stop_event = asyncio.Event()

    async def _time_watcher() -> None:
        await asyncio.sleep(_FILL_MAX_SECONDS)
        stop_event.set()
        logger.info(f"[FillImageText] 达到时间上限 {_FILL_MAX_SECONDS / 3600:.1f}h，后续任务将跳过，剩余记录下一轮（下个月）继续")

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

    def _note_error(kind: str, detail: str) -> None:
        """错误按类型聚合计数；同一类型只在首次出现时 WARNING 一次，其余等轮末汇总。"""
        err_kinds[kind] += 1
        if kind not in seen_errors:
            seen_errors.add(kind)
            logger.warning(f"[FillImageText] 首次出现「{kind}」：{detail[:300]}（同类错误后续只计数，轮末汇总）")

    def _on_download_attempt(table_name: str, record_id: int, url: str, kind: str, exc: BaseException) -> None:
        """单个候选地址下载失败的回调：记 DEBUG + 维护主图片服务健康度。"""
        msg = str(exc) or type(exc).__name__
        if primary_prefix and url.startswith(primary_prefix) and kind == "transient":
            primary_state["fails"] = int(primary_state["fails"]) + 1  # type: ignore[operator]
            if not primary_state["down"] and int(primary_state["fails"]) >= _FILL_PRIMARY_DOWN_THRESHOLD:
                primary_state["down"] = True
                logger.warning(f"[FillImageText] 主图片服务 {_FILL_IMAGE_BASE_URL} 连续 {primary_state['fails']} 次 5xx/连不上，本轮判定不可用，后续记录改用公网/旧站候选（请排查该服务）")
        logger.debug(f"[FillImageText] 候选地址失败 table={table_name} id={record_id} kind={kind} url={url}: {msg[:160]}")

    async def _parse(fetched: FetchedImage, client: httpx.AsyncClient, table_name: str, record_id: int) -> tuple[str, str, int]:
        """MinerU 解析：backend 逐个回退，每个 backend 重试若干次。

        返回 (markdown, 错误摘要, 服务级失败次数)。服务级失败 = 非 MinerUError 的异常
        （MinerU 5xx / 连不上 / 对端断开 / 超时），用于判断 MinerU 整体是否挂了；
        MinerUError（这张图解析不出来）不计入。
        """
        last_error = ""
        service_fails = 0
        for bi, backend in enumerate(_FILL_BACKENDS):
            for attempt in range(1, _FILL_MAX_RETRIES + 1):
                try:
                    markdown = await convert_bytes_to_markdown(
                        fetched.data,
                        fetched.filename,
                        content_type=fetched.content_type,
                        client=client,
                        backend=backend,
                    )
                    return markdown, "", service_fails
                except MinerUError as e:
                    last_error = f"backend={backend} MinerU 错误: {e}"
                except Exception as e:  # noqa: BLE001 —— MinerU 服务 5xx / 连接抖动等
                    last_error = f"backend={backend} {type(e).__name__}: {e}"
                    service_fails += 1
                if attempt < _FILL_MAX_RETRIES:
                    logger.debug(f"[FillImageText] 解析失败将重试 table={table_name} id={record_id}: {last_error[:160]}")
                    await asyncio.sleep(_FILL_RETRY_DELAY)
            if bi < len(_FILL_BACKENDS) - 1:
                logger.debug(f"[FillImageText] backend={backend} 全部重试失败，回退到 backend={_FILL_BACKENDS[bi + 1]}  table={table_name} id={record_id}")
        return "", last_error, service_fails

    async def _worker(worker_no: int, client: httpx.AsyncClient) -> None:
        nonlocal ok_count, fail_count, dead_count, processed, ok_via_backup
        while True:
            item = await q.get()
            try:
                if item is None:  # 毒丸：退出信号
                    return
                if stop_event.is_set():
                    return  # 剩余记录不计入日志，由汇总统计 not_done
                model_cls, record_id, file_name, image_field = item
                table_name = str(model_cls.Meta.table)

                candidates = build_image_candidates(
                    file_name=file_name,
                    image=image_field,
                    base_url=_FILL_IMAGE_BASE_URL,
                    public_base_url=_FILL_IMAGE_PUBLIC_BASE_URL,
                    legacy_host=_FILL_IMAGE_LEGACY_HOST,
                )
                if primary_state["down"] and primary_prefix:
                    candidates = [u for u in candidates if not u.startswith(primary_prefix)]

                t0 = time.monotonic()
                processed += 1

                if not candidates:
                    detail = f"table={table_name} id={record_id} file_name={str(file_name)[:60]!r} image={str(image_field or '')[:80]!r}"
                    if primary_prefix or (_FILL_IMAGE_PUBLIC_BASE_URL or "").strip():
                        # 地址前缀已配置却拼不出候选 → 该行字段本身没有可用图片地址（占位图/脏值），熔断
                        dead_count += 1
                        _note_error("no_candidate_dead", detail)
                        await _write_log(table_name, record_id, file_name, _STATUS_DEAD, 0, "file_name / image 均无可用图片地址")
                    else:
                        fail_count += 1
                        _note_error("no_candidate_config", detail)
                        await _write_log(table_name, record_id, file_name, _STATUS_FAILED, 0, "图片地址前缀未配置（JGH_IMAGE_BASE_URL / JGH_IMAGE_PUBLIC_BASE_URL）")
                    continue

                # ① 下载：候选地址逐个尝试
                try:
                    fetched = await fetch_first_available(
                        client,
                        candidates,
                        timeout=_FILL_DOWNLOAD_TIMEOUT,
                        filename_hint=file_name,
                        on_attempt=lambda url, kind, exc, tn=table_name, rid=record_id: _on_download_attempt(tn, rid, url, kind, exc),
                    )
                except ImageFetchError as e:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    detail = f"table={table_name} id={record_id} {e.summary()}"
                    if e.permanent:
                        dead_count += 1
                        _note_error("image_gone_4xx", detail)
                        await _write_log(table_name, record_id, file_name, _STATUS_DEAD, elapsed_ms, e.summary())
                    else:
                        fail_count += 1
                        _note_error("image_download_transient", detail)
                        await _write_log(table_name, record_id, file_name, _STATUS_FAILED, elapsed_ms, e.summary())
                    continue

                # ② 解析：backend 回退 + 重试
                markdown, parse_error, service_fails = await _parse(fetched, client, table_name, record_id)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                if markdown:
                    mineru_state["fails"] = 0  # 成功即清零：要抓的是"连续"不可用
                elif service_fails:
                    mineru_state["fails"] = int(mineru_state["fails"]) + service_fails  # type: ignore[operator]
                    if not mineru_state["down"] and int(mineru_state["fails"]) >= _FILL_MINERU_DOWN_THRESHOLD:
                        mineru_state["down"] = True
                        stop_event.set()
                        logger.error(f"[FillImageText] MinerU {settings.MINERU_BASE_URL} 连续 {mineru_state['fails']} 次服务级失败（5xx/连不上/断开），判定不可用，本轮提前结束；请排查 MinerU 服务")
                if not markdown:
                    fail_count += 1
                    _note_error("mineru_service_error" if service_fails else "mineru_parse_failed", f"table={table_name} id={record_id} file={fetched.filename} {parse_error}")
                    await _write_log(table_name, record_id, file_name, _STATUS_FAILED, elapsed_ms, parse_error)
                    continue

                try:
                    await model_cls.filter(id=record_id).update(word=markdown)
                except Exception as e:  # noqa: BLE001
                    fail_count += 1
                    _note_error("db_write_failed", f"table={table_name} id={record_id}: {e}")
                    await _write_log(table_name, record_id, file_name, _STATUS_FAILED, elapsed_ms, f"写入 word 失败：{e}")
                    continue

                ok_count += 1
                if fetched.url != candidates[0]:
                    ok_via_backup += 1
                await _write_log(table_name, record_id, file_name, _STATUS_OK, elapsed_ms)
                if processed % _FILL_PROGRESS_EVERY == 0:
                    logger.info(
                        f"[FillImageText] 进度 {processed}/{total}：成功 {ok_count}（备用地址 {ok_via_backup}），失败 {fail_count}，熔断 {dead_count}，耗时 {(time.time() - started) / 60:.1f}min"
                    )
            finally:
                q.task_done()

    # 投入毒丸让每个 worker 退出
    for _ in range(_FILL_CONCURRENCY):
        await q.put(None)

    limits = httpx.Limits(max_connections=_FILL_CONCURRENCY * 4, max_keepalive_connections=_FILL_CONCURRENCY * 2)
    try:
        # 全体 worker 共用一个客户端：图片下载与 MinerU 调用都复用连接
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=_FILL_DOWNLOAD_TIMEOUT), limits=limits) as client:
            await asyncio.gather(*[_worker(i, client) for i in range(_FILL_CONCURRENCY)])
    finally:
        watcher.cancel()  # 无论正常结束还是被取消，都清理 watcher

    # ── 4. 汇总日志 ───────────────────────────────────────────────────────────────
    elapsed = time.time() - started
    not_done = total - ok_count - fail_count - dead_count  # 因时间上限/提前结束未处理的记录，下一轮（下个月）续跑
    if mineru_state["down"]:
        stop_reason = "MinerU 服务不可用，提前结束"
    elif stop_event.is_set():
        stop_reason = f"时间上限 {_FILL_MAX_SECONDS / 3600:.1f}h"
    else:
        stop_reason = "全部完成"
    logger.info(f"[FillImageText] 本轮结束（{stop_reason}）：成功 {ok_count}（其中 {ok_via_backup} 条靠备用地址），失败 {fail_count}，熔断 {dead_count}，未处理 {not_done}，耗时 {elapsed / 3600:.2f}h")
    if err_kinds:
        logger.warning("[FillImageText] 失败分类：" + "，".join(f"{k}×{v}" for k, v in err_kinds.most_common(10)))
    if primary_state["down"]:
        logger.warning(f"[FillImageText] 主图片服务 {_FILL_IMAGE_BASE_URL} 本轮判定不可用（连续 5xx/连不上），请排查该服务")


async def _last_fill_run_at() -> datetime | None:
    """fill_log 里最新一行的写入时间（= 上一轮真实跑过的时间标记）；表为空返回 None。"""
    from app.models.standard.image_fill_log import StandardImageFillLog

    rows = await StandardImageFillLog.all().order_by("-create_time").limit(1).values_list("create_time", flat=True)
    if not rows:
        return None
    last = rows[0]
    return last if isinstance(last, datetime) else None


async def _run_fill_image_text_monthly() -> None:
    """每月定时入口（每月 1 号 04:20）。带重入守卫：已有一轮在跑则跳过本次触发。"""
    global _fill_running
    if _fill_running:
        logger.info("[FillImageText] 已有一轮在跑，本次触发跳过")
        return
    _fill_running = True
    try:
        await _fill_image_text_round()
    finally:
        _fill_running = False


async def _run_fill_image_text_catchup() -> None:
    """进程启动后的补跑入口（启动 _FILL_CATCHUP_DELAY_MINUTES 分钟后触发一次）。

    月度周期 + 频繁部署 = 很容易正好错过每月触发时刻，所以启动时按「距上次真实跑过多久」判断：
    超过 _FILL_CATCHUP_AFTER_DAYS 天（或从没跑过）才补一轮，否则直接跳过。
    判据取 fill_log 最新一行的 create_time；注意一轮跑到一半被重启打断时日志里已有新行，
    本次启动不会续跑，剩余记录等下个月的定时轮次（或手动把 _FILL_CATCHUP_AFTER_DAYS 调小）。
    """
    global _fill_running
    if _fill_running:
        return
    try:
        last = await _last_fill_run_at()
    except Exception as e:  # noqa: BLE001 —— DB 不可达等，补跑直接放弃
        logger.warning(f"[FillImageText] 启动补跑检查失败（忽略）：{e}")
        return

    if last is not None:
        now = datetime.now(timezone.utc) if last.tzinfo else datetime.now()
        age_days = (now - last).days
        if age_days < _FILL_CATCHUP_AFTER_DAYS:
            logger.info(f"[FillImageText] 距上次回填 {age_days} 天（< {_FILL_CATCHUP_AFTER_DAYS}），启动补跑跳过")
            return
        logger.info(f"[FillImageText] 距上次回填已 {age_days} 天，启动补跑一轮")
    else:
        logger.info("[FillImageText] 回填日志为空（从未跑过），启动补跑一轮")

    _fill_running = True
    try:
        await _fill_image_text_round()
    finally:
        _fill_running = False


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

    # 标准库 / 向量库维护类任务仅 standard（行业版）变体注册；
    # generic（通用版）没有标准库数据源，整组跳过
    std_brand = _is_standard_brand()
    if not std_brand:
        logger.info(f"[Scheduler] 标准库/向量库维护任务不启用（BRAND_VARIANT={settings.BRAND_VARIANT}，仅 standard 变体维护）")

    # 凌晨 02:30 跑标准库指纹增量同步（排在向量库同步之前，新增章节当天可入库向量）
    # STD_SYNC_DAILY_ENABLED=false 时关闭；非 standard 变体不注册
    if not std_brand:
        pass
    elif (os.getenv("STD_SYNC_DAILY_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on"):
        sched.add_job(
            _run_std_data_daily,
            trigger=CronTrigger(hour=2, minute=30),
            id="std_data_daily",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info("[Scheduler] std_data_daily 已启用（每日 02:30）")
    else:
        logger.info("[Scheduler] std_data_daily 已禁用（STD_SYNC_DAILY_ENABLED=false）")

    # 凌晨 03:30 跑标准向量库增量构建（与 nian_feed 错开）
    # STANDARD_VEC_DAILY_ENABLED=false 时关闭——首次全量构建期建议关闭，跑完再打开
    # 非 standard 变体不注册
    if not std_brand:
        pass
    elif (os.getenv("STANDARD_VEC_DAILY_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on"):
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

    # 每月 1 号 04:20 触发一次，单轮最长跑 96h（4 天）后停止提交新任务，剩余记录留到下个月
    # 另注册一次性「启动补跑」：距上次真实跑过 ≥ _FILL_CATCHUP_AFTER_DAYS 天（或从没跑过）才补一轮，
    # 覆盖「触发时刻正好停机 / 频繁部署错过窗口」——月度周期不像原来 12h 那样很快自然续上
    # 非 standard 变体不注册（标准文档表格/公式 OCR 回填同属标准库维护）
    if not std_brand:
        pass
    elif (os.getenv("FILL_IMAGE_TEXT_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on"):
        sched.add_job(
            _run_fill_image_text_monthly,
            trigger=CronTrigger(day=1, hour=4, minute=20),
            id="fill_image_text_monthly",
            replace_existing=True,
            coalesce=True,
            max_instances=1,  # 防止上一轮未跑完时重叠触发
            misfire_grace_time=86400,  # 月度任务：触发被推迟 1 天内仍补跑
        )
        sched.add_job(
            _run_fill_image_text_catchup,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(minutes=_FILL_CATCHUP_DELAY_MINUTES)),
            id="fill_image_text_catchup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=600,
        )
        logger.info(f"[Scheduler] fill_image_text_monthly 已启用（每月 1 号 04:20，单轮上限 {_FILL_MAX_SECONDS / 3600:.0f}h；启动 {_FILL_CATCHUP_DELAY_MINUTES} 分钟后按需补跑）")
    else:
        logger.info("[Scheduler] fill_image_text_monthly 已禁用（FILL_IMAGE_TEXT_ENABLED=false）")

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

    # dsh HTTP 桥回合登记的 token（try 内登记、收尾清理只清自己的条目）
    _turn_token = None

    try:
        # 构建 agent（复用 qa.py 的基础设施）
        from pathlib import Path
        from app.api.v1.ai.qa import _user_workspace, _session_tmp_dir
        from app.langchain.agents.qa_agent import create_qa_agent
        from app.services.agent_runtime.call_context import (
            AgentCallContext,
            set_agent_call_context,
            clear_agent_call_context,
        )

        ws = _user_workspace(task.user_id)
        _session_tmp_dir(ws, session_key)  # 确保 session 临时目录存在
        # dsh 内核：共享 MCP 工具不再经 extra_tools 注入（阶段 2 走 MCP 桥恢复）

        # 按角色模型配置：定时任务代表用户执行，同样守角色配置。解析 + set 请求级
        # CTX（CTX_PROFILE / CTX_GEN_BLOCK_OVERRIDE 随 to_thread 拷贝进构建线程，
        # 生成能力门卫与 chat 块构建自动跟随）。is_super 不传，定时任务不扩权。
        # 对话模式偏好同样生效：模式块覆盖 profile 后必须重设 CTX_PROFILE
        # （resolve_user_model_profile 内部已 set 过原始 profile）。
        from app.langchain.role_model_profile import CTX_PROFILE, apply_gen_override, resolve_user_model_profile

        _profile = await resolve_user_model_profile(task.user_id)

        from app.langchain.chat_mode import resolve_user_chat_pref

        # fallback_block 传角色块：level 归一（块档位白名单 + 平滑迁移）对准真实生效块
        _chat_mode, _mode_block, _chat_level = await resolve_user_chat_pref(task.user_id, fallback_block=_profile.chat_block_key)
        if _mode_block:
            import dataclasses

            from app.langchain.config import chat_block_supports_vision

            _profile = dataclasses.replace(_profile, chat_block_key=_mode_block, supports_vision=chat_block_supports_vision(_mode_block))
            CTX_PROFILE.set(_profile)
        apply_gen_override(_profile)

        def _build():
            return create_qa_agent(
                root_dir=str(ws),
                user_id=task.user_id,
                chat_block_key=_profile.chat_block_key,
                thinking_level=_chat_level,
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

        # dsh HTTP 桥：登记本回合逐消息上下文（产物联动），token 持有至收尾清理
        try:
            from app.mcp_bridge.dsh_http_bridge import set_active_turn

            _turn_token = set_active_turn(task.user_id, session_id=session.id, session_key=session_key, message_id=assistant_msg.id, workspace=ws)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[ScheduledTask] set_active_turn 失败: {_e}")

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
        try:
            from app.mcp_bridge.dsh_http_bridge import clear_active_turn

            clear_active_turn(task.user_id, _turn_token)
        except Exception:  # noqa: BLE001
            pass
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
        try:
            from app.mcp_bridge.dsh_http_bridge import clear_active_turn

            clear_active_turn(task.user_id, _turn_token)
        except Exception:  # noqa: BLE001
            pass
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
