"""
AI 用户使用看板（仅 R_SUPER 可见）

聚合 qa / workbench / nian 三个 AI 入口的用户使用数据：
- /ai/dashboard/overview          总览卡片（含消费总额）
- /ai/dashboard/trend             每日趋势（metric=message/session/activeUser/credit/yuan）
- /ai/dashboard/users             用户排行（分页，含消费列）
- /ai/dashboard/users/{user_id}   单用户明细（含消费分项）
- /ai/dashboard/cost-meta         计费口径元信息（汇率、显示名）
- /ai/dashboard/usage-records     明细流水（分页，多过滤）
- /ai/dashboard/pricing           当前生效价（GET）
- /ai/dashboard/pricing/upsert    改价/新增（POST，事务）
- /ai/dashboard/pricing/history   单条目历史（GET）

底层数据：
- agent_session / agent_message（standard 库，OceanBase）
- agent_skill（standard 库）
- agent_usage_log / agent_pricing（standard 库）
- users（system 库）
所有库底层都是同一个 OceanBase 实例，可跨表 JOIN。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from tortoise import Tortoise
from tortoise.transactions import in_transaction

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.core.ctx import CTX_USER_ID
from app.models.system import User
from app.schemas.base import Fail, Success, SuccessExtra

router = APIRouter(prefix="/dashboard", tags=["AI 用户看板"])


# ── helpers ──────────────────────────────────────────────────────────────────

async def _ensure_super() -> Optional[Fail]:
    """非超管直接返回 Fail；超管返回 None。"""
    _, _, is_super = await get_current_role_codes()
    if not is_super:
        return Fail(code="4032", msg="forbidden: 仅管理员可访问")
    return None


def _parse_range(start: Optional[str], end: Optional[str]) -> tuple[datetime, datetime]:
    """解析 [start, end) 区间；缺省给近 30 天。end 视为不含。"""
    now = datetime.now()
    if end:
        try:
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            end_dt = now
    else:
        end_dt = now
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
        except ValueError:
            start_dt = end_dt - timedelta(days=30)
    else:
        start_dt = end_dt - timedelta(days=30)
    if start_dt > end_dt:
        start_dt = end_dt - timedelta(days=30)
    return start_dt, end_dt


def _modules_filter(modules: Optional[str]) -> set[str]:
    """modules 参数：逗号分隔，支持 qa / nian / workbench。空表示全选。"""
    if not modules:
        return {"qa", "nian", "workbench"}
    parts = {m.strip().lower() for m in modules.split(",") if m.strip()}
    valid = parts & {"qa", "nian", "workbench"}
    return valid or {"qa", "nian", "workbench"}


async def _fetch_user_map(user_ids: list[int]) -> dict[int, dict[str, Any]]:
    """批量取用户名/昵称（system 库）。"""
    if not user_ids:
        return {}
    users = await User.filter(id__in=user_ids).values("id", "user_name", "nick_name")
    return {u["id"]: u for u in users}


# ── /overview ────────────────────────────────────────────────────────────────

@router.get("/overview", summary="看板总览卡片")
async def overview(
    start: Optional[str] = Query(None, description="开始时间 ISO，含"),
    end: Optional[str] = Query(None, description="结束时间 ISO，不含"),
    modules: Optional[str] = Query(None, description="qa,nian,workbench 逗号分隔，默认全选"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    start_dt, end_dt = _parse_range(start, end)
    mods = _modules_filter(modules)
    conn = Tortoise.get_connection("conn_standard")

    # ── 活跃用户数：在区间内有任意一条 message / session 创建即算活跃 ──
    active_users_sql = (
        "SELECT COUNT(DISTINCT t.user_id) AS c FROM ("
        "  SELECT s.user_id FROM agent_session s "
        "  WHERE s.user_id IS NOT NULL AND s.create_time >= %s AND s.create_time < %s "
        "  UNION "
        "  SELECT s.user_id FROM agent_session s JOIN agent_message m ON m.session_id = s.id "
        "  WHERE s.user_id IS NOT NULL AND m.create_time >= %s AND m.create_time < %s "
        ") t"
    )

    session_sql = (
        "SELECT COUNT(*) AS c FROM agent_session s "
        "WHERE s.create_time >= %s AND s.create_time < %s"
    )

    message_sql = (
        "SELECT COUNT(*) AS c FROM agent_message "
        "WHERE create_time >= %s AND create_time < %s"
    )
    error_message_sql = (
        "SELECT COUNT(*) AS c FROM agent_message "
        "WHERE create_time >= %s AND create_time < %s AND status = 'error'"
    )
    skill_sql = (
        "SELECT COUNT(*) AS c FROM agent_skill "
        "WHERE create_time >= %s AND create_time < %s"
    )

    p2 = [start_dt, end_dt]
    p4 = p2 * 2

    _, active_rows = await conn.execute_query(active_users_sql, p4)
    active_users = int(active_rows[0]["c"]) if active_rows else 0

    msg_count = err_count = skill_count = 0
    session_count = 0
    if "qa" in mods or "nian" in mods:
        _, r = await conn.execute_query(session_sql, p2)
        session_count = int(r[0]["c"]) if r else 0
    if "qa" in mods or "nian" in mods:
        _, r = await conn.execute_query(message_sql, p2)
        msg_count = int(r[0]["c"]) if r else 0
        _, r = await conn.execute_query(error_message_sql, p2)
        err_count = int(r[0]["c"]) if r else 0
    if "workbench" in mods:
        _, r = await conn.execute_query(skill_sql, p2)
        skill_count = int(r[0]["c"]) if r else 0

    # ── 消费总额（按 module 分组）：所有 agent_usage_log 记录都计 ──────
    cost_sql = (
        "SELECT module, SUM(cost_yuan) AS yuan, SUM(credits) AS credits "
        "FROM agent_usage_log WHERE create_time >= %s AND create_time < %s "
        "GROUP BY module"
    )
    _, cost_rows = await conn.execute_query(cost_sql, p2)
    cost_by_module: dict[str, dict[str, float]] = {}
    total_yuan = 0.0
    total_credits = 0.0
    for r in cost_rows:
        m = r["module"] or "unknown"
        y = float(r["yuan"] or 0)
        c = float(r["credits"] or 0)
        cost_by_module[m] = {"yuan": y, "credits": c}
        total_yuan += y
        total_credits += c

    return Success(data={
        "activeUsers": active_users,
        "sessionCount": session_count,
        "messageCount": msg_count,
        "errorMessageCount": err_count,
        "skillCount": skill_count,
        "totalYuan": round(total_yuan, 4),
        "totalCredits": round(total_credits, 2),
        "costByModule": cost_by_module,
        "rangeStart": start_dt.isoformat(),
        "rangeEnd": end_dt.isoformat(),
    })


# ── /trend ───────────────────────────────────────────────────────────────────

@router.get("/trend", summary="按天趋势")
async def trend(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    metric: str = Query("message", description="message / session / activeUser / credit / yuan"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    start_dt, end_dt = _parse_range(start, end)
    conn = Tortoise.get_connection("conn_standard")

    metric = metric.lower()
    if metric == "message":
        sql = (
            "SELECT DATE(create_time) AS d, COUNT(*) AS c FROM agent_message "
            "WHERE create_time >= %s AND create_time < %s "
            "GROUP BY DATE(create_time) ORDER BY d ASC"
        )
    elif metric == "session":
        sql = (
            "SELECT DATE(create_time) AS d, COUNT(*) AS c FROM agent_session "
            "WHERE create_time >= %s AND create_time < %s "
            "GROUP BY DATE(create_time) ORDER BY d ASC"
        )
    elif metric in ("activeuser", "activeusers"):
        sql = (
            "SELECT DATE(t.dt) AS d, COUNT(DISTINCT t.user_id) AS c FROM ("
            "  SELECT s.create_time AS dt, s.user_id FROM agent_session s "
            "    WHERE s.user_id IS NOT NULL AND s.create_time >= %s AND s.create_time < %s "
            "  UNION ALL "
            "  SELECT m.create_time AS dt, s.user_id FROM agent_message m "
            "    JOIN agent_session s ON s.id = m.session_id "
            "    WHERE s.user_id IS NOT NULL AND m.create_time >= %s AND m.create_time < %s "
            ") t GROUP BY DATE(t.dt) ORDER BY d ASC"
        )
        params = [start_dt, end_dt] * 2
        _, rows = await conn.execute_query(sql, params)
        return Success(data={"metric": metric, "points": [
            {"date": str(r["d"]), "value": int(r["c"])} for r in rows
        ]})
    elif metric == "credit":
        sql = (
            "SELECT DATE(create_time) AS d, COALESCE(SUM(credits), 0) AS c "
            "FROM agent_usage_log "
            "WHERE create_time >= %s AND create_time < %s "
            "GROUP BY DATE(create_time) ORDER BY d ASC"
        )
    elif metric == "yuan":
        sql = (
            "SELECT DATE(create_time) AS d, COALESCE(SUM(cost_yuan), 0) AS c "
            "FROM agent_usage_log "
            "WHERE create_time >= %s AND create_time < %s "
            "GROUP BY DATE(create_time) ORDER BY d ASC"
        )
    else:
        return Fail(msg=f"未知 metric: {metric}")

    _, rows = await conn.execute_query(sql, [start_dt, end_dt])
    # 补齐零值，便于前端折线连续
    by_date = {str(r["d"]): float(r["c"]) for r in rows}
    points: list[dict[str, Any]] = []
    cursor = datetime(start_dt.year, start_dt.month, start_dt.day)
    end_day = datetime(end_dt.year, end_dt.month, end_dt.day)
    while cursor <= end_day:
        key = cursor.strftime("%Y-%m-%d")
        v = by_date.get(key, 0)
        # 整数 metric 转回 int，cost 类保持 float（前端能识别）
        points.append({"date": key, "value": int(v) if metric in ("message", "session") else round(v, 2)})
        cursor += timedelta(days=1)
    return Success(data={"metric": metric, "points": points})


# ── /users ───────────────────────────────────────────────────────────────────

@router.get("/users", summary="用户排行（分页）")
async def users_rank(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None, description="按用户名/昵称模糊搜索"),
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    order_by: str = Query("credits", description="credits/messageCount/sessionCount/skillCount/lastActiveAt"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    start_dt, end_dt = _parse_range(start, end)
    conn = Tortoise.get_connection("conn_standard")

    # 1) 按 user_id 在 standard 库聚合
    agg_sql = (
        "SELECT t.user_id AS user_id, "
        "  SUM(t.session_cnt) AS session_cnt, "
        "  SUM(t.message_cnt) AS message_cnt, "
        "  SUM(t.skill_cnt) AS skill_cnt, "
        "  SUM(t.cost_yuan) AS cost_yuan, "
        "  SUM(t.credits) AS credits, "
        "  MAX(t.last_active) AS last_active "
        "FROM ("
        "  SELECT s.user_id AS user_id, COUNT(*) AS session_cnt, 0 AS message_cnt, 0 AS skill_cnt, 0 AS cost_yuan, 0 AS credits, MAX(s.update_time) AS last_active "
        "    FROM agent_session s WHERE s.user_id IS NOT NULL AND s.create_time >= %s AND s.create_time < %s "
        "    GROUP BY s.user_id "
        "  UNION ALL "
        "  SELECT s.user_id AS user_id, 0, COUNT(*), 0, 0, 0, MAX(m.create_time) AS last_active "
        "    FROM agent_message m JOIN agent_session s ON s.id = m.session_id "
        "    WHERE s.user_id IS NOT NULL AND m.create_time >= %s AND m.create_time < %s "
        "    GROUP BY s.user_id "
        "  UNION ALL "
        "  SELECT sk.user_id AS user_id, 0, 0, COUNT(*), 0, 0, MAX(sk.create_time) AS last_active "
        "    FROM agent_skill sk WHERE sk.user_id IS NOT NULL AND sk.create_time >= %s AND sk.create_time < %s "
        "    GROUP BY sk.user_id "
        "  UNION ALL "
        "  SELECT u.user_id AS user_id, 0, 0, 0, "
        "         COALESCE(SUM(u.cost_yuan), 0), COALESCE(SUM(u.credits), 0), "
        "         MAX(u.create_time) AS last_active "
        "    FROM agent_usage_log u WHERE u.user_id IS NOT NULL AND u.create_time >= %s AND u.create_time < %s "
        "    GROUP BY u.user_id "
        ") t GROUP BY t.user_id"
    )
    _, rows = await conn.execute_query(agg_sql, [start_dt, end_dt] * 4)

    # 2) 关联用户名（system 库）
    user_ids = [int(r["user_id"]) for r in rows if r["user_id"] is not None]
    user_map = await _fetch_user_map(user_ids)

    # 3) keyword 过滤（在内存里做，量级有限）
    enriched: list[dict[str, Any]] = []
    kw = (keyword or "").strip().lower()
    for r in rows:
        uid = int(r["user_id"])
        u = user_map.get(uid)
        user_name = (u or {}).get("user_name") or f"#{uid}"
        nick_name = (u or {}).get("nick_name") or ""
        if kw and kw not in user_name.lower() and kw not in nick_name.lower():
            continue
        last_active = r["last_active"]
        enriched.append({
            "userId": uid,
            "userName": user_name,
            "nickName": nick_name,
            "sessionCount": int(r["session_cnt"] or 0),
            "messageCount": int(r["message_cnt"] or 0),
            "skillCount": int(r["skill_cnt"] or 0),
            "costYuan": round(float(r["cost_yuan"] or 0), 4),
            "credits": round(float(r["credits"] or 0), 2),
            "lastActiveAt": int(last_active.timestamp() * 1000) if isinstance(last_active, datetime) else None,
        })

    # 4) 排序
    sort_key_map = {
        "credits": "credits",
        "costYuan": "costYuan",
        "messageCount": "messageCount",
        "sessionCount": "sessionCount",
        "skillCount": "skillCount",
        "lastActiveAt": "lastActiveAt",
    }
    sk = sort_key_map.get(order_by, "credits")
    enriched.sort(key=lambda x: (x.get(sk) or 0), reverse=True)

    # 5) 分页
    total = len(enriched)
    offset = (current - 1) * size
    page = enriched[offset: offset + size]

    return SuccessExtra(data={"records": page}, total=total, current=current, size=size)


# ── /users/{user_id} ─────────────────────────────────────────────────────────

@router.get("/users/{user_id}", summary="单用户使用明细")
async def user_detail(
    user_id: int,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    start_dt, end_dt = _parse_range(start, end)
    conn = Tortoise.get_connection("conn_standard")

    user = await User.get_or_none(id=user_id)
    if user is None:
        return Fail(code="4040", msg="用户不存在")

    # 概览
    overview_sql = (
        "SELECT "
        "  (SELECT COUNT(*) FROM agent_session WHERE user_id=%s AND create_time>=%s AND create_time<%s) AS session_cnt, "
        "  (SELECT COUNT(*) FROM agent_message m JOIN agent_session s ON s.id=m.session_id "
        "     WHERE s.user_id=%s AND m.create_time>=%s AND m.create_time<%s) AS message_cnt, "
        "  (SELECT COUNT(*) FROM agent_skill WHERE user_id=%s AND create_time>=%s AND create_time<%s) AS skill_cnt"
    )
    p = [user_id, start_dt, end_dt] * 3
    _, ov_rows = await conn.execute_query(overview_sql, p)
    ov = ov_rows[0] if ov_rows else {}

    # 最近会话
    sess_sql = (
        "SELECT id, session_key, title, message_count, create_time, update_time "
        "FROM agent_session WHERE user_id=%s AND create_time>=%s AND create_time<%s "
        "ORDER BY update_time DESC LIMIT %s"
    )
    _, sess_rows = await conn.execute_query(sess_sql, [user_id, start_dt, end_dt, limit])
    sessions = [{
        "id": r["id"],
        "sessionKey": r["session_key"],
        "title": r["title"],
        "messageCount": int(r["message_count"] or 0),
        "createdAt": int(r["create_time"].timestamp() * 1000) if r["create_time"] else None,
        "updatedAt": int(r["update_time"].timestamp() * 1000) if r["update_time"] else None,
    } for r in sess_rows]

    # 凝练技能（按区间）
    skill_sql = (
        "SELECT id, skill_key, name, source, visibility, is_enabled, create_time "
        "FROM agent_skill WHERE user_id=%s AND create_time>=%s AND create_time<%s "
        "ORDER BY create_time DESC LIMIT %s"
    )
    _, sk_rows = await conn.execute_query(skill_sql, [user_id, start_dt, end_dt, limit])
    skills = [{
        "id": r["id"],
        "skillKey": r["skill_key"],
        "name": r["name"],
        "source": r["source"],
        "visibility": r["visibility"],
        "isEnabled": bool(r["is_enabled"]),
        "createdAt": int(r["create_time"].timestamp() * 1000) if r["create_time"] else None,
    } for r in sk_rows]

    # 消费汇总：按 module 分组 + 原始量纲累计
    cost_sql = (
        "SELECT module, "
        "  COALESCE(SUM(cost_yuan), 0) AS yuan, "
        "  COALESCE(SUM(credits), 0) AS credits, "
        "  COUNT(*) AS cnt "
        "FROM agent_usage_log WHERE user_id=%s AND create_time>=%s AND create_time<%s "
        "GROUP BY module"
    )
    _, cost_rows = await conn.execute_query(cost_sql, [user_id, start_dt, end_dt])
    cost_by_module: dict[str, dict[str, Any]] = {}
    total_yuan = 0.0
    total_credits = 0.0
    for r in cost_rows:
        m = r["module"] or "unknown"
        y = float(r["yuan"] or 0)
        c = float(r["credits"] or 0)
        cost_by_module[m] = {
            "yuan": round(y, 4),
            "credits": round(c, 2),
            "count": int(r["cnt"] or 0),
        }
        total_yuan += y
        total_credits += c

    # 原始量纲累计：units_json 里的 token_in / token_out / video_sec_*  / mcp_call
    units_sql = (
        "SELECT units_json FROM agent_usage_log "
        "WHERE user_id=%s AND create_time>=%s AND create_time<%s AND units_json IS NOT NULL"
    )
    _, units_rows = await conn.execute_query(units_sql, [user_id, start_dt, end_dt])
    raw_units: dict[str, float] = {}
    import json as _json
    for r in units_rows:
        u = r["units_json"]
        if isinstance(u, str):
            try:
                u = _json.loads(u)
            except Exception:
                continue
        if not isinstance(u, dict):
            continue
        for k, v in u.items():
            try:
                raw_units[k] = raw_units.get(k, 0) + float(v or 0)
            except (TypeError, ValueError):
                continue

    return Success(data={
        "user": {
            "userId": user.id,
            "userName": user.user_name,
            "nickName": user.nick_name,
        },
        "overview": {
            "sessionCount": int(ov.get("session_cnt") or 0),
            "messageCount": int(ov.get("message_cnt") or 0),
            "skillCount": int(ov.get("skill_cnt") or 0),
        },
        "costSummary": {
            "totalYuan": round(total_yuan, 4),
            "totalCredits": round(total_credits, 2),
            "byModule": cost_by_module,
            "rawUnits": {k: round(v, 2) for k, v in raw_units.items()},
        },
        "sessions": sessions,
        "skills": skills,
        "rangeStart": start_dt.isoformat(),
        "rangeEnd": end_dt.isoformat(),
    })


# ── /cost-meta ───────────────────────────────────────────────────────────────

@router.get("/cost-meta", summary="计费口径元信息（汇率、显示名）")
async def cost_meta():
    """前端展示积分时读取此接口；R_SUPER 之外也返回，避免影响普通页面误读时崩。"""
    from app.langchain.config import langchain_config
    rate = float(langchain_config.CREDIT_RATE_YUAN or 0.001)
    return Success(data={
        "creditRateYuan": rate,
        "creditsPerYuan": round(1.0 / rate, 2) if rate > 0 else 0,
        "creditName": "积分",
        "currencyName": "元",
    })


# ── /usage-records 明细流水 ──────────────────────────────────────────────────

@router.get("/usage-records", summary="使用流水明细（分页，可过滤）")
async def usage_records(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    module: Optional[str] = Query(None, description="chat/vision/embed/mcp/video"),
    biz_entry: Optional[str] = Query(None, description="qa/nian/workbench"),
    model: Optional[str] = Query(None, description="model id 模糊匹配"),
    provider: Optional[str] = Query(None),
    min_credits: Optional[float] = Query(None, ge=0, description="最低积分阈值，用于找贵的"),
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    start_dt, end_dt = _parse_range(start, end)
    conn = Tortoise.get_connection("conn_standard")

    where = ["create_time >= %s", "create_time < %s"]
    params: list[Any] = [start_dt, end_dt]
    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)
    if module:
        where.append("module = %s")
        params.append(module)
    if biz_entry:
        where.append("biz_entry = %s")
        params.append(biz_entry)
    if model:
        where.append("model LIKE %s")
        params.append(f"%{model}%")
    if provider:
        where.append("provider = %s")
        params.append(provider)
    if min_credits is not None:
        where.append("credits >= %s")
        params.append(min_credits)
    where_sql = " AND ".join(where)

    # 先查总数
    count_sql = f"SELECT COUNT(*) AS c FROM agent_usage_log WHERE {where_sql}"
    _, c_rows = await conn.execute_query(count_sql, params)
    total = int(c_rows[0]["c"]) if c_rows else 0

    # 分页拉数据
    offset = (current - 1) * size
    list_sql = (
        "SELECT id, create_time, user_id, module, biz_entry, provider, model, "
        "       units_json, cost_yuan, credits, session_id, ref_type, ref_id "
        "FROM agent_usage_log "
        f"WHERE {where_sql} "
        "ORDER BY create_time DESC LIMIT %s OFFSET %s"
    )
    _, rows = await conn.execute_query(list_sql, params + [size, offset])

    # 关联用户名
    uids = list({int(r["user_id"]) for r in rows if r["user_id"] is not None})
    user_map = await _fetch_user_map(uids)

    import json as _json
    records: list[dict[str, Any]] = []
    for r in rows:
        uid = r["user_id"]
        u = user_map.get(int(uid)) if uid is not None else None
        units = r.get("units_json")
        if isinstance(units, str):
            try:
                units = _json.loads(units)
            except Exception:
                units = None
        ct = r["create_time"]
        records.append({
            "id": int(r["id"]),
            "createdAt": int(ct.timestamp() * 1000) if ct else None,
            "userId": int(uid) if uid is not None else None,
            "userName": (u or {}).get("user_name") if u else None,
            "nickName": (u or {}).get("nick_name") if u else None,
            "module": r["module"],
            "bizEntry": r.get("biz_entry"),
            "provider": r["provider"],
            "model": r.get("model"),
            "units": units or {},
            "costYuan": float(r["cost_yuan"] or 0),
            "credits": float(r["credits"] or 0),
            "sessionId": r.get("session_id"),
            "refType": r.get("ref_type"),
            "refId": r.get("ref_id"),
        })

    return SuccessExtra(data={"records": records}, total=total, current=current, size=size)


# ── /pricing 单价管理 ────────────────────────────────────────────────────────

@router.get("/pricing", summary="当前生效单价表")
async def pricing_list(
    keyword: Optional[str] = Query(None, description="按 model 模糊搜索"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    conn = Tortoise.get_connection("conn_standard")
    where = ["effective_to IS NULL"]
    params: list[Any] = []
    if keyword:
        where.append("(model LIKE %s OR provider LIKE %s)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    sql = (
        "SELECT id, provider, model, unit_type, price_yuan, note, effective_from, create_time "
        "FROM agent_pricing "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY provider, model, unit_type"
    )
    _, rows = await conn.execute_query(sql, params)

    from app.langchain.config import langchain_config
    rate = float(langchain_config.CREDIT_RATE_YUAN or 0.001)

    items = []
    for r in rows:
        price_yuan = float(r["price_yuan"] or 0)
        items.append({
            "id": int(r["id"]),
            "provider": r["provider"],
            "model": r["model"],
            "unitType": r["unit_type"],
            "priceYuan": price_yuan,
            "creditsPerUnit": round(price_yuan / rate, 4) if rate > 0 else 0,
            "note": r.get("note"),
            "effectiveFrom": int(r["effective_from"].timestamp() * 1000) if r["effective_from"] else None,
        })
    return Success(data={"items": items, "creditRateYuan": rate})


@router.post("/pricing/upsert", summary="改价 / 新增（事务：失效旧 + 插新）")
async def pricing_upsert(
    payload: dict = Body(..., description="{provider, model, unitType, priceYuan, note?}"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    provider = (payload.get("provider") or "").strip()
    model = (payload.get("model") or "").strip()
    unit_type = (payload.get("unitType") or "").strip()
    note = (payload.get("note") or "").strip() or None
    raw_price = payload.get("priceYuan")

    if not provider or not model or not unit_type:
        return Fail(msg="provider / model / unitType 不能为空")
    try:
        price = Decimal(str(raw_price))
    except (InvalidOperation, TypeError, ValueError):
        return Fail(msg=f"priceYuan 不是合法数字: {raw_price}")
    if price < 0:
        return Fail(msg="priceYuan 不能为负数")

    # 在事务内：旧行 effective_to=NOW()，再插一条新行
    async with in_transaction("conn_standard") as conn:
        await conn.execute_query(
            "UPDATE agent_pricing SET effective_to = NOW(6) "
            "WHERE provider=%s AND model=%s AND unit_type=%s AND effective_to IS NULL",
            [provider, model, unit_type],
        )
        await conn.execute_query(
            "INSERT INTO agent_pricing(provider, model, unit_type, price_yuan, effective_from, note) "
            "VALUES (%s, %s, %s, %s, NOW(6), %s)",
            [provider, model, unit_type, str(price), note],
        )

    # 立即失效缓存：下次 LLM 调用就用新价
    try:
        from app.langchain.billing import Billing
        Billing.invalidate_cache()
    except Exception:
        pass

    return Success(msg="改价成功，秒级生效")


@router.get("/pricing/history", summary="单条目的历史版本")
async def pricing_history(
    provider: str = Query(...),
    model: str = Query(...),
    unit_type: str = Query(..., alias="unitType"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    conn = Tortoise.get_connection("conn_standard")
    sql = (
        "SELECT id, price_yuan, note, effective_from, effective_to, create_time "
        "FROM agent_pricing "
        "WHERE provider=%s AND model=%s AND unit_type=%s "
        "ORDER BY effective_from DESC"
    )
    _, rows = await conn.execute_query(sql, [provider, model, unit_type])
    items = [{
        "id": int(r["id"]),
        "priceYuan": float(r["price_yuan"] or 0),
        "note": r.get("note"),
        "effectiveFrom": int(r["effective_from"].timestamp() * 1000) if r["effective_from"] else None,
        "effectiveTo": int(r["effective_to"].timestamp() * 1000) if r["effective_to"] else None,
        "isCurrent": r["effective_to"] is None,
    } for r in rows]
    return Success(data={
        "provider": provider, "model": model, "unitType": unit_type,
        "items": items,
    })


# ── /sessions/{session_key}/messages 会话消息详情 ───────────────────────────

@router.get("/sessions/{session_key}/messages", summary="会话消息列表")
async def session_messages(session_key: str):
    if (resp := await _ensure_super()) is not None:
        return resp

    conn = Tortoise.get_connection("conn_standard")

    _, sess_rows = await conn.execute_query(
        "SELECT id, title, message_count, create_time, update_time "
        "FROM agent_session WHERE session_key=%s AND is_deleted=0",
        [session_key],
    )
    if not sess_rows:
        return Fail(code="4040", msg="会话不存在")
    sess = sess_rows[0]
    session_id = sess["id"]

    import json as _json
    _, msg_rows = await conn.execute_query(
        "SELECT id, role, content, thinking, tool_steps_json, status, error, create_time "
        "FROM agent_message WHERE session_id=%s ORDER BY id ASC",
        [session_id],
    )

    messages = []
    for r in msg_rows:
        tool_steps = r["tool_steps_json"]
        if isinstance(tool_steps, str):
            try:
                tool_steps = _json.loads(tool_steps)
            except Exception:
                tool_steps = None
        messages.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"] or "",
            "thinking": r["thinking"] or None,
            "toolSteps": tool_steps or [],
            "status": r["status"],
            "error": r["error"] or None,
            "createdAt": int(r["create_time"].timestamp() * 1000) if r["create_time"] else None,
        })

    return Success(data={
        "sessionKey": session_key,
        "title": sess["title"] or "未命名会话",
        "messageCount": int(sess["message_count"] or 0),
        "createdAt": int(sess["create_time"].timestamp() * 1000) if sess["create_time"] else None,
        "updatedAt": int(sess["update_time"].timestamp() * 1000) if sess["update_time"] else None,
        "messages": messages,
    })


# ── /users/{user_id}/sessions 用户全量会话分页 ───────────────────────────────

@router.get("/users/{user_id}/sessions", summary="用户全量会话（分页）")
async def user_sessions(
    user_id: int,
    keyword: Optional[str] = Query(None, description="按标题模糊搜索"),
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    conn = Tortoise.get_connection("conn_standard")

    where = ["user_id = %s", "is_deleted = 0"]
    params: list[Any] = [user_id]
    if keyword and keyword.strip():
        where.append("title LIKE %s")
        params.append(f"%{keyword.strip()}%")

    where_sql = " AND ".join(where)

    _, c_rows = await conn.execute_query(
        f"SELECT COUNT(*) AS c FROM agent_session WHERE {where_sql}", params
    )
    total = int(c_rows[0]["c"]) if c_rows else 0

    offset = (current - 1) * size
    _, rows = await conn.execute_query(
        f"SELECT id, session_key, title, message_count, create_time, update_time "
        f"FROM agent_session WHERE {where_sql} "
        f"ORDER BY update_time DESC LIMIT %s OFFSET %s",
        params + [size, offset],
    )

    sessions = [{
        "id": r["id"],
        "sessionKey": r["session_key"],
        "title": r["title"] or "未命名会话",
        "messageCount": int(r["message_count"] or 0),
        "createdAt": int(r["create_time"].timestamp() * 1000) if r["create_time"] else None,
        "updatedAt": int(r["update_time"].timestamp() * 1000) if r["update_time"] else None,
    } for r in rows]

    return SuccessExtra(data={"records": sessions}, total=total, current=current, size=size)


# ── /credit-quotas 用户积分配额 ──────────────────────────────────────────────

@router.get("/credit-quotas", summary="用户积分配额列表（含余额，分页）")
async def credit_quotas(
    keyword: Optional[str] = Query(None, description="按用户名/昵称模糊搜索"),
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    from app.settings import APP_SETTINGS
    conn = Tortoise.get_connection("conn_standard")

    # 获取所有用户及其配额
    all_users = await User.all().values("id", "user_name", "nick_name")
    kw = (keyword or "").strip().lower()
    if kw:
        all_users = [u for u in all_users if kw in (u["user_name"] or "").lower() or kw in (u["nick_name"] or "").lower()]

    if not all_users:
        return SuccessExtra(data={"records": []}, total=0, current=current, size=size)

    user_ids = [u["id"] for u in all_users]

    # 批量查配额（无记录即用默认值）
    _, quota_rows = await conn.execute_query(
        f"SELECT user_id, quota FROM agent_user_credit_quota WHERE user_id IN ({','.join(['%s']*len(user_ids))})",
        user_ids,
    )
    quota_map = {int(r["user_id"]): int(r["quota"]) for r in quota_rows}

    # 批量查已用积分
    _, used_rows = await conn.execute_query(
        f"SELECT user_id, COALESCE(SUM(credits),0) AS used "
        f"FROM agent_usage_log WHERE user_id IN ({','.join(['%s']*len(user_ids))}) GROUP BY user_id",
        user_ids,
    )
    used_map = {int(r["user_id"]): float(r["used"]) for r in used_rows}

    default_quota = APP_SETTINGS.DEFAULT_CREDIT_QUOTA
    records = []
    for u in all_users:
        uid = int(u["id"])
        quota = quota_map.get(uid, default_quota)
        used = used_map.get(uid, 0.0)
        records.append({
            "userId": uid,
            "userName": u["user_name"] or f"#{uid}",
            "nickName": u["nick_name"] or "",
            "quota": quota,
            "used": round(used, 2),
            "remaining": round(quota - used, 2),
        })

    # 按余额升序排（快用完的排前面）
    records.sort(key=lambda x: x["remaining"])
    total = len(records)
    offset = (current - 1) * size
    page = records[offset: offset + size]
    return SuccessExtra(data={"records": page}, total=total, current=current, size=size)


@router.post("/credit-quotas/{user_id}", summary="设置单用户积分配额（管理员）")
async def set_credit_quota(
    user_id: int,
    payload: dict = Body(..., description="{quota: number}"),
):
    if (resp := await _ensure_super()) is not None:
        return resp

    quota = payload.get("quota")
    try:
        quota_int = int(quota)
        if quota_int < 0:
            raise ValueError
    except (TypeError, ValueError):
        return Fail(msg="quota 必须是非负整数")

    user = await User.get_or_none(id=user_id)
    if user is None:
        return Fail(code="4040", msg="用户不存在")

    conn = Tortoise.get_connection("conn_standard")
    # UPSERT
    await conn.execute_query(
        "INSERT INTO agent_user_credit_quota(user_id, quota) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE quota=%s, update_time=NOW(6)",
        [user_id, quota_int, quota_int],
    )
    return Success(msg="配额已更新")


# ── /my-credit 当前用户积分余额（登录用户自查）────────────────────────────────

@router.get("/my-credit", summary="当前用户积分余额与配额")
async def my_credit():
    from app.settings import APP_SETTINGS
    uid = CTX_USER_ID.get()
    if not uid:
        return Fail(code="4000", msg="未登录")

    conn = Tortoise.get_connection("conn_standard")

    # 查配额
    _, quota_rows = await conn.execute_query(
        "SELECT quota FROM agent_user_credit_quota WHERE user_id=%s", [uid]
    )
    quota = int(quota_rows[0]["quota"]) if quota_rows else APP_SETTINGS.DEFAULT_CREDIT_QUOTA

    # 查已用
    _, used_rows = await conn.execute_query(
        "SELECT COALESCE(SUM(credits),0) AS used FROM agent_usage_log WHERE user_id=%s", [uid]
    )
    used = float(used_rows[0]["used"]) if used_rows else 0.0

    is_unlimited = APP_SETTINGS.BRAND_VARIANT.lower() == "standard"

    return Success(data={
        "quota": quota if not is_unlimited else -1,
        "used": round(used, 2),
        "remaining": round(quota - used, 2) if not is_unlimited else -1,
        "isUnlimited": is_unlimited,
    })
