"""
用户积分配额检查。

BRAND_VARIANT=standard 时直接放行（无限制）；
BRAND_VARIANT=generic 时查询用户已用积分，超出配额时返回 QuotaExceeded。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class QuotaStatus:
    allowed: bool
    quota: int
    used: float
    remaining: float
    is_unlimited: bool


async def check_quota(user_id: Optional[int]) -> QuotaStatus:
    """检查用户积分配额，返回 QuotaStatus。失败安全：查询出错时放行。"""
    from app.settings import APP_SETTINGS

    if APP_SETTINGS.BRAND_VARIANT.lower() == "standard":
        return QuotaStatus(allowed=True, quota=-1, used=0, remaining=-1, is_unlimited=True)

    if not user_id:
        return QuotaStatus(allowed=True, quota=-1, used=0, remaining=-1, is_unlimited=True)

    try:
        from tortoise import Tortoise
        conn = Tortoise.get_connection("conn_standard")

        _, quota_rows = await conn.execute_query(
            "SELECT quota FROM agent_user_credit_quota WHERE user_id=%s", [user_id]
        )
        quota = int(quota_rows[0]["quota"]) if quota_rows else APP_SETTINGS.DEFAULT_CREDIT_QUOTA

        _, used_rows = await conn.execute_query(
            "SELECT COALESCE(SUM(credits),0) AS used FROM agent_usage_log WHERE user_id=%s",
            [user_id],
        )
        used = float(used_rows[0]["used"]) if used_rows else 0.0
        remaining = quota - used

        return QuotaStatus(
            allowed=remaining > 0,
            quota=quota,
            used=round(used, 2),
            remaining=round(remaining, 2),
            is_unlimited=False,
        )
    except Exception:
        # 查询失败时放行，避免影响业务
        return QuotaStatus(allowed=True, quota=-1, used=0, remaining=-1, is_unlimited=True)
