from contextlib import asynccontextmanager
from datetime import datetime

import asyncio
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app.api.v1.utils import refresh_api_list
from app.core.exceptions import SettingNotFound
from app.core.init_app import (
    init_menus,
    init_users,
    make_middlewares,
    modify_db,
    register_db,
    register_exceptions,
    register_routers,
)
from app.core.redis import close_redis, init_redis
from app.langchain.vector_store import get_vector_store
# 顶部 import：把 seekdb + 其依赖的 langchain 模块的冷启动开销提前消化，避免 lifespan 超时
from app.services.seekdb import ensure_kb_collection as _ensure_kb_collection
from app.log import log
from app.models.system import Log, LogDetailType, LogType

try:
    from app.settings import APP_SETTINGS
except ImportError:
    raise SettingNotFound("Can not import settings")


def create_app() -> FastAPI:
    if APP_SETTINGS.DEBUG:
        _app = FastAPI(
            title=APP_SETTINGS.APP_TITLE,
            description=APP_SETTINGS.APP_DESCRIPTION,
            version=APP_SETTINGS.VERSION,
            openapi_url="/openapi.json",
            middleware=make_middlewares(),
            lifespan=lifespan
        )
    else:
        _app = FastAPI(
            title=APP_SETTINGS.APP_TITLE,
            description=APP_SETTINGS.APP_DESCRIPTION,
            version=APP_SETTINGS.VERSION,
            openapi_url=None,
            middleware=make_middlewares(),
            lifespan=lifespan
        )
    register_db(_app)
    register_exceptions(_app)
    register_routers(_app, prefix="/api")
    return _app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_time = datetime.now()
    log.info("[lifespan] 启动中：连接 Redis…")
    _app.state.redis = await init_redis()
    FastAPICache.init(RedisBackend(_app.state.redis), prefix="fastapi-cache")
    log.info("[lifespan] Redis OK")

    # 清理 compare-smart 比对锁的残留 key。
    # 这些锁是 Redis SET NX PX（TTL 2h）防重复提交，进程被 kill / 重启时不会主动释放，
    # 残留期间同标准对的比对会被 _smart_lock_wait 轮询卡住直到 TTL 到期。
    # 重启时主动 SCAN+DEL，让锁的存活和进程生命周期对齐。
    # 前缀来源：app/api/v1/ai/ai_comparison_v2.py 的 _SMART_LOCK_PREFIX / _LOCK_PREFIX。
    try:
        _lock_prefixes = ("smart_cmp_lock:", "smart_v2_cmp_lock:")
        _deleted = 0
        for _prefix in _lock_prefixes:
            _cursor = 0
            while True:
                _cursor, _keys = await _app.state.redis.scan(
                    cursor=_cursor, match=f"{_prefix}*", count=100
                )
                if _keys:
                    await _app.state.redis.delete(*_keys)
                    _deleted += len(_keys)
                if _cursor == 0:
                    break
        if _deleted:
            log.info(f"[lifespan] 清理 compare-smart 残留比对锁 {_deleted} 个")
        else:
            log.info(f"[lifespan] compare-smart 无残留比对锁")
    except Exception as _e:
        log.warning(f"[lifespan] 清理残留比对锁失败（不影响启动）: {_e}")

    # 绑定 Billing 的主 loop（Tortoise 连接池绑这条 loop）。
    # 子线程 / 子 loop（如 seekdb 的同步桥接）调 Billing.record 时会 schedule 回这里写库，
    # 避免 "got Future attached to a different loop"。
    try:
        from app.langchain.billing import Billing
        Billing.bind_loop()
    except Exception as _e:
        log.warning(f"[lifespan] Billing.bind_loop 失败（计费可能跨 loop 时失效）：{_e}")
    try:
        log.info("[lifespan] modify_db()…")
        await modify_db()
        log.info("[lifespan] modify_db OK；init_menus()…")
        await init_menus()
        log.info("[lifespan] init_menus OK；refresh_api_list()…")
        await refresh_api_list()
        log.info("[lifespan] refresh_api_list OK；init_users()…")
        await init_users()
        log.info("[lifespan] init_users OK")

        # 模型切换：把 DB 里的全局模型选择读进内存映射（失败用 .env 默认块兜底，不阻塞启动）
        try:
            from app.langchain.model_selection import load_active_blocks_from_db

            await load_active_blocks_from_db()
            log.info("[lifespan] 激活模型块已加载")
        except Exception as _e:
            log.warning(f"[lifespan] 激活模型块加载失败（用 .env 默认块）: {_e}")

        # 标准查重向量库已改为手动构建，不在启动时自动加载
        # 使用 POST /api/v1/ai/deduplication/rebuild-index 接口手动构建

        from app.services.agent_runtime.skill_seed import seed_builtin_skills
        log.info("[lifespan] workspace 目录迁移开始…")
        # 老路径迁移：.qa_workspace → .agent_workspace；.agent_skills → .agent_workspace/.agent_skills
        try:
            import shutil as _shutil
            from pathlib import Path as _P
            _proj = _P(__file__).resolve().parent.parent
            old_ws = _proj / ".qa_workspace"
            new_ws = _proj / ".agent_workspace"
            if old_ws.exists() and not new_ws.exists():
                old_ws.rename(new_ws)
                log.info(f"已迁移目录 {old_ws.name} -> {new_ws.name}")
            new_ws.mkdir(exist_ok=True)
            # .agent_skills 搬进 workspace
            skills_in_ws = new_ws / ".agent_skills"
            old_skills = _proj / ".agent_skills"
            if old_skills.exists() and old_skills.is_dir() and not old_skills.is_symlink():
                if not skills_in_ws.exists():
                    old_skills.rename(skills_in_ws)
                    log.info("已迁移 .agent_skills -> .agent_workspace/.agent_skills")
                else:
                    # 两边都有：把老目录里的子目录逐个搬过去
                    for child in old_skills.iterdir():
                        dst = skills_in_ws / child.name
                        if not dst.exists():
                            child.rename(dst)
                    _shutil.rmtree(old_skills, ignore_errors=True)
                    log.info("已合并 .agent_skills 到 .agent_workspace/.agent_skills")
            elif old_skills.is_symlink():
                old_skills.unlink()
            skills_in_ws.mkdir(exist_ok=True)
            # 清理 workspace 里可能残留的旧软链
            link = new_ws / ".agent_skills"
            if link.is_symlink():
                link.unlink()
                link.mkdir(exist_ok=True)

            # 老路径迁移：.agent_workspace/uploads/<key>/ → .agent_workspace/sessions/<key>/uploads/
            try:
                legacy_uploads = new_ws / "uploads"
                sessions_root = new_ws / "sessions"
                if legacy_uploads.is_dir() and not legacy_uploads.is_symlink():
                    moved = 0
                    for sess_dir in legacy_uploads.iterdir():
                        if not sess_dir.is_dir():
                            continue
                        target = sessions_root / sess_dir.name / "uploads"
                        target.mkdir(parents=True, exist_ok=True)
                        for f in sess_dir.iterdir():
                            dst = target / f.name
                            if not dst.exists():
                                f.rename(dst)
                                moved += 1
                        try:
                            sess_dir.rmdir()
                        except OSError:
                            pass
                    try:
                        legacy_uploads.rmdir()
                    except OSError:
                        pass
                    if moved:
                        log.info(f"已迁移 {moved} 个上传文件到 sessions/<key>/uploads/")
            except Exception as _e:
                log.warning(f"uploads 目录迁移失败: {_e}")
        except Exception as _e:
            log.warning(f"workspace 目录初始化失败: {_e}")
        log.info("[lifespan] workspace 迁移完成；seed_builtin_skills…")
        await seed_builtin_skills()
        log.info("[lifespan] seed_builtin_skills OK")

        # 个人知识库 seekdb collection 启动时初始化（带超时；失败不阻塞应用启动）
        try:
            log.info("[lifespan] seekdb ensure_kb_collection…")
            await asyncio.wait_for(asyncio.to_thread(_ensure_kb_collection), timeout=30)
            log.info("[lifespan] seekdb KB collection 就绪")
        except asyncio.TimeoutError:
            log.warning("[lifespan] seekdb 初始化超时（>30s），跳过；KB 功能首次调用时再尝试")
        except Exception as _e:
            log.warning(f"[lifespan] seekdb KB collection 初始化失败（KB 功能不可用）: {_e}")

        # 向量表为空时自动触发全量构建（fire-and-forget，不阻塞启动）
        _auto_build_task = None
        try:
            async def _auto_build_vec_if_empty():
                try:
                    import aiomysql as _aiomysql
                    from app.langchain.embedding_providers import vec_table_suffix
                    from app.services.mysql_pool import standard_pool
                    from app.services.standard_vec.builder import StandardVecBuilder

                    s = vec_table_suffix()
                    meta_tbl = f"standard_vec_meta{s}"
                    async with standard_pool.acquire() as conn:
                        async with conn.cursor(_aiomysql.DictCursor) as cur:
                            await cur.execute(f"SELECT COUNT(*) AS c FROM {meta_tbl}")
                            row = await cur.fetchone()
                    if row and row["c"] > 0:
                        log.info(f"[lifespan] {meta_tbl} 已有数据，跳过自动构建")
                        return
                    log.info(f"[lifespan] {meta_tbl} 为空，开始自动构建…")
                    builder = StandardVecBuilder()
                    meta_r = await builder.build_meta()
                    log.info(f"[lifespan] auto-build meta: {meta_r}")
                    chapter_r = await builder.build_chapter()
                    log.info(f"[lifespan] auto-build chapter: {chapter_r}")
                except Exception as e:
                    log.warning(f"[lifespan] vec auto-build 失败（不影响应用）: {e}")

            _auto_build_task = asyncio.create_task(_auto_build_vec_if_empty())
        except Exception as _e:
            log.warning(f"[lifespan] vec auto-build 启动失败（不影响应用）: {_e}")

        # 共享资源预热（MCP tools / memory store）：否则启动后第一个用户请求要等冷启动
        # （本地实测约 19s）。fire-and-forget，不阻塞启动；失败则首个请求懒加载兜底。
        _warmup_shared_task = None
        try:
            async def _warmup_shared_resources():
                try:
                    from app.api.v1.ai.qa import _get_shared_resources
                    await _get_shared_resources()
                    log.info("[lifespan] 共享资源（MCP tools / memory store）预热完成")
                except Exception as e:
                    log.warning(f"[lifespan] 共享资源预热失败（首个请求时懒加载，不影响应用）: {e}")

            _warmup_shared_task = asyncio.create_task(_warmup_shared_resources())
        except Exception as _e:
            log.warning(f"[lifespan] 共享资源预热启动失败（不影响应用）: {_e}")

        # 续跑：扫描重启前残留的 processing 查重批次（受 AUTO_RESUME_DEDUP_BATCH 开关控制）
        try:
            from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

            stuck = await StandardDuplicateBatch.filter(status="processing").values_list("id", flat=True)
            if stuck:
                if APP_SETTINGS.AUTO_RESUME_DEDUP_BATCH:
                    import asyncio as _asyncio

                    from app.langchain.agents.tasks.standard.standard_deduplication_agent import resume_batch_dispatch

                    # 把僵尸 running 改回 pending（服务被强杀时遗留的中间态）
                    reset_count = await StandardDuplicateName.filter(
                        batch_id__in=list(stuck), task_status="running"
                    ).update(task_status="pending")
                    log.info(f"检测到 {len(stuck)} 个未完成的查重批次，重置 {reset_count} 条僵尸 running，将后台续跑：{list(stuck)}")
                    for _bid in stuck:
                        _asyncio.create_task(resume_batch_dispatch(_bid))
                else:
                    log.info(
                        f"检测到 {len(stuck)} 个未完成的查重批次：{list(stuck)}，"
                        f"AUTO_RESUME_DEDUP_BATCH=false，跳过自动续跑（可前端手动触发 /batch-resume）"
                    )
        except Exception as _e:
            log.warning(f"扫描续跑批次失败: {_e}")

        await Log.create(log_type=LogType.SystemLog, log_detail_type=LogDetailType.SystemStart)

        # 知识库：启动夜间 feed 排序调度器
        try:
            from app.core.scheduler import start as _start_nian_scheduler
            _start_nian_scheduler()
        except Exception as _e:
            log.warning(f"[lifespan] 知识库调度器启动失败（不影响应用）：{_e}")

        # 定时任务：恢复 active 状态的 AgentScheduledTask 到 APScheduler
        try:
            from app.core.scheduler import restore_scheduled_tasks
            await restore_scheduled_tasks()
        except Exception as _e:
            log.warning(f"[lifespan] 定时任务恢复失败（不影响应用）：{_e}")

        yield

    finally:
        # 取消 fire-and-forget 后台任务，避免 "Task was destroyed but it is pending"
        for _bg_task in (_auto_build_task, _warmup_shared_task):
            if _bg_task is not None and not _bg_task.done():
                _bg_task.cancel()
                try:
                    await _bg_task
                except (asyncio.CancelledError, Exception):
                    pass

        end_time = datetime.now()
        runtime = (end_time - start_time).total_seconds() / 60
        log.info(f"App {_app.title} runtime: {runtime} min")  # noqa
        await Log.create(log_type=LogType.SystemLog, log_detail_type=LogDetailType.SystemStop)
        # 知识库：关闭调度器
        try:
            from app.core.scheduler import shutdown as _shutdown_nian_scheduler
            await _shutdown_nian_scheduler()
        except Exception as _e:
            log.warning(f"关闭知识库调度器失败：{_e}")
        await close_redis(_app.state.redis)
        try:
            from app.services.mysql_pool import close_pool as _close_mysql_pool
            await _close_mysql_pool()
        except Exception as _e:
            log.warning(f"关闭 mysql 池失败：{_e}")
        try:
            from app.langchain.checkpointers import close_checkpointer as _close_ckpt
            await _close_ckpt()
        except Exception as _e:
            log.warning(f"关闭 checkpointer 失败：{_e}")


app = create_app()

_data_dir = APP_SETTINGS.BASE_DIR / "data"
_data_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=APP_SETTINGS.STATIC_ROOT), name="static")
app.mount("/data", StaticFiles(directory=_data_dir), name="data")


def _register_frontend_spa(app_: FastAPI) -> None:
    """SERVE_FRONTEND=true 时让后端直接托管 web/dist（桌面单机版用，默认关闭，Docker 走 nginx 不受影响）。

    以 catch-all 路由注册在最后：/api、/static、/data 均在前面已注册、优先命中，本路由只兜底其余路径。
    SPA history 模式下，未命中静态文件的 GET 一律回退 index.html。
    """
    if os.getenv("SERVE_FRONTEND", "").lower() not in ("1", "true", "yes"):
        return
    dist = APP_SETTINGS.BASE_DIR / "web" / "dist"
    index = dist / "index.html"
    if not index.exists():
        return

    @app_.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str):
        first = full_path.split("/", 1)[0]
        if first in ("api", "static", "data"):
            raise StarletteHTTPException(status_code=404)
        target = dist / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(index)


_register_frontend_spa(app)
