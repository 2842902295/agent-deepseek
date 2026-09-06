"""
tongdao_chat_message 对话历史分析工具（旧标准问答系统）

每行 = 一组 Q&A（question + answer 同行）。用法（库由 ENV_FILE 决定）：

    # 表结构 + 模块分布 + 头部用户
    python -m app.scripts.analyze_tongdao_chat stats

    # 候选案例会话扫描：按模块找出「问得好 + 答得好」的完整会话
    python -m app.scripts.analyze_tongdao_chat candidates [apply_id]

    # 关键词检索（在 question/answer 中匹配）
    python -m app.scripts.analyze_tongdao_chat search <keyword> [apply_id]

    # 导出单个会话全文（截断显示，用于人工审阅）
    python -m app.scripts.analyze_tongdao_chat conv <conversation_id> [每段截断字数]

    # 导出某用户的行为流水（按时间序）
    python -m app.scripts.analyze_tongdao_chat user <creator> [limit]

内置排除内部测试账号（来源：用户分析报告）：492 754 752 765 549 3197 602
"""

import asyncio
import sys

from tortoise import Tortoise

from app.settings.config import settings

# 内部测试账号（分析报告认定，统计/候选时排除）
INTERNAL_CREATORS = {"492", "754", "752", "765", "549", "3197", "602"}

_EXCLUDE = ",".join(f"'{c}'" for c in INTERNAL_CREATORS)


async def _init() -> None:
    settings._build_tortoise_orm()
    await Tortoise.init(config=settings.TORTOISE_ORM)


def _conn():
    return Tortoise.get_connection("conn_standard")


async def cmd_stats() -> None:
    await _init()
    conn = _conn()

    _, rows = await conn.execute_query("SHOW CREATE TABLE tongdao_chat_message")
    print("=" * 78)
    print("表结构")
    print("=" * 78)
    print(rows[0]["Create Table"] if rows else "(无)")

    _, total = await conn.execute_query(
        "SELECT COUNT(*) n, COUNT(DISTINCT creator) users, COUNT(DISTINCT conversation_id) convs "
        "FROM tongdao_chat_message WHERE deleted=0"
    )
    print()
    print(f"总消息: {total[0]['n']}  独立用户: {total[0]['users']}  会话数: {total[0]['convs']}")

    _, dist = await conn.execute_query(
        "SELECT apply_id, COUNT(*) n, COUNT(DISTINCT creator) users "
        "FROM tongdao_chat_message WHERE deleted=0 GROUP BY apply_id ORDER BY n DESC"
    )
    print()
    print("模块分布 (apply_id | 消息 | 用户)")
    for r in dist:
        print(f"  {r['apply_id'] or '(空)':<18} {r['n']:<8} {r['users']}")

    _, top = await conn.execute_query(
        f"SELECT creator, COUNT(*) n, COUNT(DISTINCT conversation_id) convs "
        f"FROM tongdao_chat_message WHERE deleted=0 AND creator NOT IN ({_EXCLUDE}) "
        f"GROUP BY creator ORDER BY n DESC LIMIT 25"
    )
    print()
    print("头部真实用户 Top25 (creator | 消息 | 会话)")
    for r in top:
        print(f"  {r['creator']:<10} {r['n']:<8} {r['convs']}")

    await Tortoise.close_connections()


async def cmd_candidates(apply_id: str | None = None) -> None:
    """候选案例：会话级聚合 question/answer 总长，双高者列为候选。"""
    await _init()
    conn = _conn()

    sql = (
        "SELECT conversation_id, creator, apply_id, "
        "SUM(CHAR_LENGTH(question)) q_len, SUM(CHAR_LENGTH(answer)) a_len, "
        "COUNT(*) msgs, MIN(create_time) t0 "
        "FROM tongdao_chat_message "
        f"WHERE deleted=0 AND creator NOT IN ({_EXCLUDE}) "
        "AND conversation_id IS NOT NULL AND question IS NOT NULL "
        + ("AND apply_id = %s " if apply_id else "") +
        "GROUP BY conversation_id, creator, apply_id "
        "HAVING q_len > 60 AND a_len > 1200 "
        "ORDER BY a_len DESC LIMIT 40"
    )
    params = [apply_id] if apply_id else []
    _, rows = await conn.execute_query(sql, params)

    print(f"候选会话 Top{len(rows)}（q_len=提问总长 a_len=回答总长）")
    for r in rows:
        _, first = await conn.execute_query(
            "SELECT question, file_name FROM tongdao_chat_message "
            "WHERE conversation_id=%s AND question IS NOT NULL ORDER BY create_time LIMIT 1",
            [r["conversation_id"]],
        )
        preview = (first[0]["question"] or "").replace("\n", " ")[:70]
        att = f" [附件: {first[0]['file_name']}]" if first[0]["file_name"] else ""
        print(
            f"  [{r['apply_id']}] conv={r['conversation_id']} by {r['creator']} {r['t0']} "
            f"| 问{r['q_len']} 答{r['a_len']} {r['msgs']}轮{att} | {preview}"
        )

    await Tortoise.close_connections()


async def cmd_search(keyword: str, apply_id: str | None = None) -> None:
    await _init()
    conn = _conn()
    sql = (
        "SELECT id, conversation_id, creator, apply_id, create_time, question "
        f"FROM tongdao_chat_message WHERE deleted=0 AND creator NOT IN ({_EXCLUDE}) "
        "AND (question LIKE %s OR answer LIKE %s) "
        + ("AND apply_id = %s " if apply_id else "") +
        "ORDER BY create_time DESC LIMIT 30"
    )
    kw = f"%{keyword}%"
    params = [kw, kw] + ([apply_id] if apply_id else [])
    _, rows = await conn.execute_query(sql, params)
    print(f"命中 {len(rows)} 条（关键词：{keyword}）")
    for r in rows:
        body = (r["question"] or "").replace("\n", " ")[:120]
        print(f"  [{r['apply_id']}] conv={r['conversation_id']} by {r['creator']} {r['create_time']}: {body}")
    await Tortoise.close_connections()


async def cmd_conv(conversation_id: str, limit_chars: int = 1200) -> None:
    await _init()
    conn = _conn()
    _, rows = await conn.execute_query(
        "SELECT creator, apply_id, create_time, question, answer, file_name "
        "FROM tongdao_chat_message WHERE conversation_id=%s ORDER BY create_time, id",
        [conversation_id],
    )
    if not rows:
        print("(无此会话)")
    else:
        print(f"会话 {conversation_id} | 模块 {rows[0]['apply_id']} | 用户 {rows[0]['creator']} | {len(rows)} 轮")
        print("=" * 78)
        for i, r in enumerate(rows):
            q = (r["question"] or "").strip()
            a = (r["answer"] or "").strip()
            if len(q) > limit_chars:
                q = q[:limit_chars] + f"\n  …（提问共 {len(r['question'])} 字，已截断）"
            if len(a) > limit_chars:
                a = a[:limit_chars] + f"\n  …（回答共 {len(r['answer'])} 字，已截断）"
            att = f"\n[附件: {r['file_name']}]" if r["file_name"] else ""
            print(f"\n── [{i + 1}] 提问 {r['create_time']} ──{att}\n{q}")
            print(f"\n── [{i + 1}] 回答 ──\n{a}")
    await Tortoise.close_connections()


async def cmd_user(creator: str, limit: int = 60) -> None:
    await _init()
    conn = _conn()
    _, rows = await conn.execute_query(
        "SELECT conversation_id, apply_id, create_time, question, file_name "
        "FROM tongdao_chat_message WHERE creator=%s AND question IS NOT NULL "
        "ORDER BY create_time LIMIT %s",
        [creator, limit],
    )
    print(f"用户 {creator} 流水（前 {len(rows)} 条）")
    for r in rows:
        body = (r["question"] or "").replace("\n", " ")[:100]
        att = " [附件]" if r["file_name"] else ""
        print(f"  {r['create_time']} [{r['apply_id']}] conv={r['conversation_id']}{att}: {body}")
    await Tortoise.close_connections()


async def cmd_qa(creator: str, keyword: str, limit_chars: int = 1500) -> None:
    """提取单组 Q&A（适用于 conv=None 的单轮模板类消息，如 stdRead 解读）"""
    await _init()
    conn = _conn()
    _, rows = await conn.execute_query(
        "SELECT id, question, answer, apply_id, create_time, file_name "
        "FROM tongdao_chat_message WHERE deleted=0 AND creator=%s AND question LIKE %s "
        "ORDER BY CHAR_LENGTH(answer) DESC LIMIT 1",
        [creator, f"%{keyword}%"],
    )
    if not rows:
        print("(未命中)")
        return
    r = rows[0]
    q = (r["question"] or "").strip()
    a = (r["answer"] or "").strip()
    if len(a) > limit_chars:
        a = a[:limit_chars] + f"\n  …（回答共 {len(r['answer'])} 字，已截断）"
    att = f"\n[附件: {r['file_name']}]" if r["file_name"] else ""
    print(f"单组 Q&A id={r['id']} [{r['apply_id']}] {r['create_time']}{att}\n")
    print(f"── 提问 ──\n{q[:800]}\n\n── 回答 ──\n{a}")
    await Tortoise.close_connections()


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "stats"
    if cmd == "stats":
        asyncio.run(cmd_stats())
    elif cmd == "candidates":
        asyncio.run(cmd_candidates(argv[1] if len(argv) > 1 else None))
    elif cmd == "search" and len(argv) > 1:
        asyncio.run(cmd_search(argv[1], argv[2] if len(argv) > 2 else None))
    elif cmd == "conv" and len(argv) > 1:
        asyncio.run(cmd_conv(argv[1], int(argv[2]) if len(argv) > 2 else 1200))
    elif cmd == "user" and len(argv) > 1:
        asyncio.run(cmd_user(argv[1], int(argv[2]) if len(argv) > 2 else 60))
    elif cmd == "qa" and len(argv) > 2:
        asyncio.run(cmd_qa(argv[1], argv[2]))
    else:
        print(__doc__)
