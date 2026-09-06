"""
计费服务：把"用量 + 单价"翻译成 agent_usage_log 一条记录。

设计原则：
  - 调用方只塞 units（如 {"token_in": 1234, "token_out": 567}），单价由 Billing 自查
  - 单价 60s 缓存；改价后自动失效
  - 失败安全：任何异常只 logger.exception，不影响业务调用链路
  - 写入快照：units_json、pricing_snapshot_json 完整保留写入时的状态，便于历史对账

改价流程（直接 SQL，参考 CLAUDE.md 的 `app_standard` 走 SQL 脚本约定）：
    UPDATE agent_pricing SET effective_to = NOW()
     WHERE provider='X' AND model='Y' AND unit_type='Z' AND effective_to IS NULL;
    INSERT INTO agent_pricing(provider, model, unit_type, price_yuan, effective_from, note)
    VALUES ('X', 'Y', 'Z', 0.000xxx, NOW(), '上游调价');
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Optional

from loguru import logger

from app.core.ctx import (
    CTX_BILLING_BIZ_ENTRY,
    CTX_BILLING_SESSION_ID,
    CTX_USER_ID,
)
from app.langchain.config import langchain_config


class Billing:
    """统一计费入口。所有 record 调用失败安全（不抛异常）。"""

    # (provider, model, unit_type) -> (pricing_id, price_yuan)
    _cache: dict[tuple[str, str, str], tuple[int, Decimal]] = {}
    _loaded_at: float = 0.0
    _TTL_SEC = 60.0
    # 主 event loop（lifespan startup 时绑定）。Tortoise 连接池绑在它上。
    # 子线程 / 子 loop（如 seekdb 的 _run_coroutine_blocking）调 record 时，
    # 我们把写库任务 schedule 回主 loop，避免 "got Future attached to a different loop"。
    _main_loop: Optional[asyncio.AbstractEventLoop] = None
    # 缺价 warning 去重（同一 provider/model/unit_type 只警告一次，避免日志刷屏）
    _missing_warned: set[tuple[str, str, str]] = set()

    @classmethod
    def bind_loop(cls, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """在 FastAPI lifespan startup 里调一次：cls.bind_loop()。"""
        cls._main_loop = loop or asyncio.get_running_loop()

    @classmethod
    def invalidate_cache(cls) -> None:
        """改价后立即失效缓存，不等 60s TTL。upsert 端点调一下即可秒级生效。"""
        cls._cache = {}
        cls._loaded_at = 0.0
        cls._missing_warned.clear()

    # ── 单价查询（带缓存） ─────────────────────────────────────────────────

    @classmethod
    async def _refresh_if_stale(cls) -> None:
        if time.time() - cls._loaded_at < cls._TTL_SEC and cls._cache:
            return
        try:
            from app.models.standard.pricing import AgentPricing

            rows = await AgentPricing.filter(effective_to__isnull=True).all()
            new_cache: dict[tuple[str, str, str], tuple[int, Decimal]] = {}
            for r in rows:
                new_cache[(r.provider, r.model, r.unit_type)] = (r.id, Decimal(str(r.price_yuan)))
            cls._cache = new_cache
            cls._loaded_at = time.time()
        except Exception:
            logger.exception("[Billing] 加载单价表失败，使用旧缓存")

    @classmethod
    async def price_of(
        cls, provider: str, model: str, unit_type: str
    ) -> tuple[int, Decimal]:
        """返回 (pricing_id, price_yuan)；找不到时返回 (0, 0)，写日志但不抛。"""
        await cls._refresh_if_stale()
        hit = cls._cache.get((provider, model, unit_type))
        if hit is not None:
            return hit
        # 退化：相同 provider+unit_type 的第一条（处理 model 变体未配价的情况）
        for (p, _m, ut), v in cls._cache.items():
            if p == provider and ut == unit_type:
                return v
        key = (provider, model, unit_type)
        if key not in cls._missing_warned:
            cls._missing_warned.add(key)
            logger.warning(f"[Billing] 单价缺失 provider={provider} model={model} unit_type={unit_type}（同条目仅提示一次）")
        return (0, Decimal("0"))

    # ── 主入口 ──────────────────────────────────────────────────────────────

    @classmethod
    async def record(
        cls,
        *,
        module: str,
        provider: str,
        model: Optional[str],
        units: dict[str, float],
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        user_id: Optional[int] = None,
        session_id: Optional[int] = None,
        biz_entry: Optional[str] = None,
        success: int = 1,
    ) -> None:
        """记一条用量。失败安全（不抛异常）。

        缺省值从 ContextVar 读：
          - user_id  ← CTX_USER_ID
          - biz_entry  ← CTX_BILLING_BIZ_ENTRY
          - session_id ← CTX_BILLING_SESSION_ID

        跨 loop 处理：
          如果当前正在子 loop 里（比如 seekdb 的同步桥接线程），Tortoise 的连接池
          绑在主 loop 上，直接 await 会抛 "got Future attached to a different loop"。
          这种情况下把写库任务 schedule 回主 loop fire-and-forget。
          计费本来就不应该阻塞业务，丢一两条也比连锁炸开主流程好。
        """
        if not units:
            return

        # 在调用方的 loop 上把上下文 / 参数固化下来，再交给主 loop 写库
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # 提前从 ContextVar 取值（跨 loop 后 ContextVar 可能取不到）
        if user_id is None:
            uid = CTX_USER_ID.get()
            user_id = uid if uid else None
        if session_id is None:
            session_id = CTX_BILLING_SESSION_ID.get()
        if biz_entry is None:
            biz_entry = CTX_BILLING_BIZ_ENTRY.get()

        coro = cls._do_record(
            module=module, provider=provider, model=model, units=units,
            ref_type=ref_type, ref_id=ref_id,
            user_id=user_id, session_id=session_id, biz_entry=biz_entry,
            success=success,
        )

        main = cls._main_loop
        # 主 loop 没绑定 / 没在跑 → 退回直接 await（业务路径，能成功）
        if main is None or main.is_closed() or main is current_loop:
            try:
                await coro
            except Exception:
                logger.exception(
                    f"[Billing] 写入失败 module={module} provider={provider} model={model} units={units}"
                )
            return

        # 子 loop / 同步桥接线程 → 把 coro 丢给主 loop 异步执行，调用方不等待
        try:
            asyncio.run_coroutine_threadsafe(_safe_run(coro, module, provider, model, units), main)
        except Exception:
            logger.exception("[Billing] schedule 到主 loop 失败")

    @classmethod
    async def _do_record(
        cls,
        *,
        module: str,
        provider: str,
        model: Optional[str],
        units: dict[str, float],
        ref_type: Optional[str],
        ref_id: Optional[int],
        user_id: Optional[int],
        session_id: Optional[int],
        biz_entry: Optional[str],
        success: int,
    ) -> None:
        from app.models.standard.video_task import AgentUsageLog

        # 累加各 unit_type 的费用
        snapshot: dict[str, dict] = {}
        total = Decimal("0")
        clean_units: dict[str, float] = {}
        for unit_type, qty in units.items():
            if qty is None:
                continue
            qty_f = float(qty)
            if qty_f <= 0:
                continue
            clean_units[unit_type] = qty_f
            pid, price = await cls.price_of(provider, model or "", unit_type)
            line = price * Decimal(str(qty_f))
            snapshot[unit_type] = {
                "pricing_id": pid,
                "price": str(price),
                "qty": qty_f,
                "line": str(line),
            }
            total += line

        if not clean_units:
            return

        rate = Decimal(str(langchain_config.CREDIT_RATE_YUAN or 0.001))
        credits = (total / rate) if rate > 0 else Decimal("0")

        await AgentUsageLog.create(
            user_id=user_id,
            module=module,
            biz_entry=biz_entry,
            provider=provider,
            model=model,
            ref_type=ref_type,
            ref_id=ref_id,
            session_id=session_id,
            units_json=clean_units,
            cost_yuan=total,
            pricing_snapshot_json=snapshot,
            credits=credits,
            success=success,
        )


async def _safe_run(coro, module: str, provider: str, model: Optional[str], units: dict) -> None:
    """run_coroutine_threadsafe 提交的 coro 抛异常会留在 Future 里没人 await，写日志吞掉。"""
    try:
        await coro
    except Exception:
        logger.exception(
            f"[Billing] 跨 loop 写入失败 module={module} provider={provider} model={model} units={units}"
        )
