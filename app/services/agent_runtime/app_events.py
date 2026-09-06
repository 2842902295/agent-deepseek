"""
应用制作行数据变更事件 hub（进程内 SSE 广播）

「应用制作」app 页面与 agent 之间的实时同步通道：任何写者（页面 rows/put|del、
agent 的 update_app_rows、app MCP 桥工具效果）写 `agent_app_row` 成功后 publish，
订阅该 workflow_key 的所有 SSE 连接（html_app.py 的 GET /{token}/events，板主页面
与分享访客页面都可订阅）收到最小化事件后自行 loadRows 幂等刷新。

设计约束：
- run.py 单 worker 单事件循环 → 进程内 dict + asyncio.Queue 即完整广播域，无需 Redis。
- **载荷刻意最小化**（type/tbls/rev/by，不带行数据、不带行键）：订阅者可能是匿名
  分享访客，行数据可能受 $acl readers 保护——事件带数据 = 绕过权限名单。页面收到
  任何事件都全量 loadRows（走 token 门控 + $acl 强制的正规读通道），丢中间事件无害。
- publish 是同步 fire-and-forget：调用方都在写路径尾部，推送失败绝不影响写主链路，
  函数内部吞掉一切异常。
- 队列满丢最旧（合并语义）：页面刷新是幂等全量读，最新事件到位即可。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

# 配额：防 SSE 连接泄漏 / 恶意刷订阅（端点侧超配额回 503）
MAX_TOTAL = 1000  # 全进程订阅总数上限
MAX_PER_WF = 64  # 单个应用（workflow_key）订阅数上限
QUEUE_MAX = 64  # 单订阅队列深度（满则丢最旧）

# wf_key → 订阅队列集合；空桶在 unsubscribe 时清理
_subs: dict[str, set[asyncio.Queue]] = {}
# wf_key → 进程内单调 rev（页面可用它判断事件新旧 / 去抖合并）
_rev: dict[str, int] = {}
_subs_total = 0


def subscribe(wf_key: str) -> Optional[asyncio.Queue]:
    """登记一个订阅队列；超全局或单板配额返回 None（端点据此回 503）。"""
    global _subs_total
    if not wf_key:
        return None
    bucket = _subs.get(wf_key)
    if bucket is not None and len(bucket) >= MAX_PER_WF:
        return None
    if _subs_total >= MAX_TOTAL:
        return None
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    if bucket is None:
        bucket = set()
        _subs[wf_key] = bucket
    bucket.add(q)
    _subs_total += 1
    return q


def unsubscribe(wf_key: str, q: asyncio.Queue) -> None:
    """移除订阅（SSE 端点 finally 必调，含 CancelledError 断开路径）；空桶顺手清理。"""
    global _subs_total
    bucket = _subs.get(wf_key)
    if bucket is None:
        return
    if q in bucket:
        bucket.discard(q)
        _subs_total = max(0, _subs_total - 1)
    if not bucket:
        _subs.pop(wf_key, None)
        _rev.pop(wf_key, None)


def publish(wf_key: str, tbls: list[str], by: str) -> None:
    """广播行数据变更事件。同步、绝不抛异常（fire-and-forget，写路径尾部调用）。

    by ∈ "page"（rows/put|del 页面写）/ "mcp"（app MCP 桥工具）/ "agent"（update_app_rows）。
    无订阅者时只推进 rev 即返回（rev 桶随 unsubscribe 空桶清理，不会无限膨胀）。"""
    try:
        bucket = _subs.get(wf_key)
        n = _rev.get(wf_key, 0) + 1
        _rev[wf_key] = n
        if not bucket:
            return
        ev = {"type": "rows", "tbls": list(dict.fromkeys(tbls or [])), "rev": n, "by": by}
        for q in list(bucket):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # 丢最旧再放最新：页面收到任何事件都全量刷新，旧事件被覆盖无害
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001 —— 广播失败绝不影响写主链路
        logger.warning(f"[app_events] publish 失败（忽略） wf={wf_key}: {e!r}")


def stats() -> dict:
    """诊断用：当前订阅总数与板数。"""
    return {"subs": _subs_total, "wfs": len(_subs)}
