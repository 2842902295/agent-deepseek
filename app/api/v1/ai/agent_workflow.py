"""
Agent 共享工作流 API

人和 Agent 共读共写的工作流（Vue Flow JSON），支持：
- 创建 / 读取（全量或部分节点）/ 整体更新 / 部分合并节点 / 软删除
- 按用户列表查询

板型（board_type）：
- board：节点连线流程编排（默认），nodes/edges 存 Vue Flow JSON
- html：应用制作——agent 像开发者一样在任务目录（users/{uid}/apps/{workflow_key}/）开发
  多文件 HTML 应用，前端 iframe 画布渲染；nodes/edges 恒为空，entryReady = DB 标志 entry_ready
  （publish_html_board 落库）或本机 index.html 存在——绝不能只查本机文件：DB 共享而
  .agent_workspace 不共享的跨机部署里，本地查不到服务器上构建的文件，会把已发布的板误判空板误删。
  应用文件同理以 DB 为跨机真相源：agent 发布时刷新活动集、用户点「发布」固化版本存档
  （agent_app_file：version=0 活动集 / >=1 存档），请求侧消费前由 ensure_app_files 惰性物化回本机磁盘（对 agent 透明）。
"""

import asyncio
import html as html_lib
import json
import os
import re
import secrets
import shutil
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel, Field
from tortoise import timezone as tz

from app.core.code import Code
from app.core.ctx import CTX_USER_ID
from app.core.dependency import AuthControl, check_token
from app.models.standard.agent import AgentSession, AgentWorkflow, AgentWorkflowVersion
from app.models.system import StatusType, User
from app.schemas.base import Fail, Success
from app.utils.security import create_html_app_token

router = APIRouter(prefix="/agent-workflows", tags=["AI-共享工作流"])

# 公开子路由（挂 ai_public_router，不走全局鉴权）：分享访客入口免登录。
# 安全边界 = 能力 URL 语义：share-view 校验分享开启 + 已发布后才签发短期 share 态托管 token，
# token 权限域仅限该看板应用目录（html_app.py 门控），与登录态双向隔离
public_router = APIRouter(prefix="/agent-workflows", tags=["AI-共享工作流(公开)"])

# 应用制作任务的应用目录根（与 qa.py / agent_public.py 同款约定：项目根/.agent_workspace）
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_USERS_ROOT = _PROJECT_ROOT / ".agent_workspace" / "users"

# 合法板型
_BOARD_TYPES = {"board", "html"}


def _app_dir(user_id: Any, workflow_key: str) -> Path:
    """应用制作任务的应用目录：users/{uid}/apps/{workflow_key}/（也是 agent 文件工具眼里的 apps/{wk}/）。

    匿名兜底与 qa.py::_user_workspace 同口径（anonymous），两处必须一致，否则物化会写错目录。"""
    uid = str(user_id) if user_id else "anonymous"
    return _USERS_ROOT / uid / "apps" / workflow_key


async def _resolve_entry_ready(wf: AgentWorkflow, user_id: Any, workflow_key: str) -> bool:
    """入口就绪判定：DB 标志为准（跨机真相源），本机文件存在作为补充并顺手回填标志。

    只查本机文件在跨机部署里会误判：DB 共享而工作目录不共享时，本地实例看不到服务器上
    构建的 index.html，把已发布的板当成空板（前端空板清理会把它软删掉）。"""
    if wf.entry_ready:
        return True
    exists = await asyncio.to_thread((_app_dir(user_id, workflow_key) / "index.html").exists)
    if exists:
        wf.entry_ready = 1
        await wf.save(update_fields=["entry_ready"])
    return exists


def extract_html_preview(app_dir: Path) -> Optional[dict]:
    """从应用目录的 index.html 提取列表卡预览素材 {title, tagline, accent}：尽力而为，失败返回 None。

    发布时（publish_html_board）与列表接口兜底回填共用；结果落 agent_workflow.html_preview
    成为跨机真相源——列表接口读 DB，不依赖本机文件（工作目录不共享的部署里读不到别的实例的文件）。

    - title：`<title>` 文本（≤60 字）
    - tagline：首个 h1/h2 文本（≤40 字，与 title 相同则不重复记）
    - accent：theme-color meta 优先；否则取全文出现最多的非灰近彩色 hex（近似应用主色）"""
    try:
        text = (app_dir / "index.html").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if not text.strip():
        return None

    def _clean(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s)
        s = html_lib.unescape(s)
        return re.sub(r"\s+", " ", s).strip()

    out: dict = {}
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        t = _clean(m.group(1))[:60]
        if t:
            out["title"] = t
    m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", text, re.I | re.S)
    if m:
        tag = _clean(m.group(1))[:40]
        if tag and tag != out.get("title"):
            out["tagline"] = tag
    m = re.search(r"""name=["']theme-color["'][^>]*content=["']([^"']+)["']""", text, re.I)
    accent = m.group(1).strip() if m else ""
    if not accent:
        colors: Counter = Counter()
        for c in re.findall(r"#[0-9a-fA-F]{6}\b", text):
            c = c.lower()
            r_, g_, b_ = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            hi, lo = max(r_, g_, b_), min(r_, g_, b_)
            if hi < 45 or hi - lo < 10:  # 近黑 / 纯灰：不像应用主色
                continue
            if hi > 215 and hi - lo < 24:  # 近白
                continue
            colors[c] += 1
        if colors:
            accent = colors.most_common(1)[0][0]
    if accent:
        out["accent"] = accent
    return out or None


async def backfill_entry_ready() -> None:
    """启动回填：本机已发布（目录里有 index.html）的应用制作把 entry_ready 落库。

    覆盖存量数据（entry_ready 列上线前就已发布的板）：每台实例启动时扫自己的 apps 目录，
    发布过的就在共享 DB 里把标志立起来，其他实例随后读到的就是 DB 真相，不再依赖本机文件。"""
    def _scan() -> list:
        out = []
        if not _USERS_ROOT.is_dir():
            return out
        for user_dir in _USERS_ROOT.iterdir():
            apps_dir = user_dir / "apps"
            if not user_dir.is_dir() or not apps_dir.is_dir():
                continue
            for app_dir in apps_dir.iterdir():
                if app_dir.is_dir() and (app_dir / "index.html").is_file():
                    out.append(app_dir.name)
        return out

    keys = await asyncio.to_thread(_scan)
    if keys:
        await AgentWorkflow.filter(workflow_key__in=keys, entry_ready=0).update(entry_ready=1)


# ─────────────────────────────────────────────────────────────────────────────
# 应用制作文件与发布存档：DB 真相源（agent_app_file 表）
# ─────────────────────────────────────────────────────────────────────────────
# 应用文件（agent 写的代码 + 页面 /save 写回的数据 json）进 DB，跨机部署（DB 共享而
# 工作目录不共享）里任何实例都能按需把文件从 DB 物化回本机磁盘（ensure_app_files），
# iframe 托管与 agent 读写照常工作。机制与技能文件（agent_skill_file）同族：BLOB 存储 + 按需物化。
#
# version 字段双语义：0 = 活动集（当前工作态 + 运行期 /save 写回，可变）；>=1 = 不可变版本存档。
# **存档只在用户点「发布」时产生**（POST /{key}/publish：内容指纹与既有存档相同则不重复建版）；
# agent 的 publish_html_board 只把目录刷进活动集 + bump 版本号触发前端重载，不产存档。
# 切换版本（原「回滚」）= 目标版文件盖回磁盘 + 活动集改成目标版内容 + app_version 指针改指——
# 不再无脑复制新版本；仅当前工作态与所有存档都不同（有未固化改动）时先自动备份一版防丢失。
# 「回到任意版」的系统级真相源在 DB，不靠 agent 从对话记忆里重建文件（重建必失真）。
#
# 存档范围：应用目录内除 uploads/（用户上传文件，单文件可到 10GB，是运行期资产不是发布内容）、
# .versions/（历史磁盘存档遗留）与 *.tmp（原子写中间产物）之外的一切。
# uploads/ 一律不入库（跨机不可见，页面需容错）；单文件超 _APP_FILE_DB_MAX_BYTES 的也只留磁盘。
# 切换版本时 uploads/ 原样保留，用户文件绝不丢失。
#
# 对 agent 完全透明：agent 始终面对 apps/{workflow_key}/ 磁盘目录（write_file / edit_file 照旧），
# 入库 / 物化由系统钩子完成——qa.py 消息入口与 html_app 托管路由在消费文件前 ensure 目录就绪。
#
# 历史：存档曾是磁盘快照 apps/{key}/.versions/v{N}/。启动回填（backfill_app_files）会把既有板子的
# .versions/ 导入 DB；新代码不再写磁盘存档，旧目录留存不读。

_LIVE_VERSION = 0
_MAX_APP_VERSIONS = 5
# 单文件入库上限：应用代码 / 数据 json 实际都是 KB 级，此线纯防把巨型素材塞进表；
# 超限文件只留磁盘（接受其跨机不可见，发布返回会列出提醒）
_APP_FILE_DB_MAX_BYTES = 16 * 1024 * 1024

_VERSIONS_DIRNAME = ".versions"
# 存档 / 回滚 / 物化都要绕开的顶层目录：uploads/ 运行期资产、.versions/ 历史磁盘存档
_VERSION_EXCLUDED_DIRS = {"uploads", _VERSIONS_DIRNAME}


def _versions_root(app_dir: Path) -> Path:
    return app_dir / _VERSIONS_DIRNAME


def _iter_version_dirs(app_dir: Path) -> list:
    """按版本号升序返回 .versions/ 下的存档目录（v{N} 形态，忽略其它杂物）。"""
    root = _versions_root(app_dir)
    if not root.is_dir():
        return []
    out = []
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit():
            out.append(p)
    out.sort(key=lambda p: int(p.name[1:]))
    return out


def _scan_app_files(app_dir: Path) -> tuple:
    """扫描应用目录取可入库文件，返回 ([(相对路径, bytes)], [超限被跳过的相对路径])。

    uploads/（运行期资产）与 .versions/（历史磁盘存档）不入库；*.tmp 是原子写中间产物忽略。"""
    files: list = []
    skipped: list = []
    if not app_dir.is_dir():
        return files, skipped
    for p in sorted(app_dir.rglob("*")):
        if not p.is_file() or p.name.endswith(".tmp"):
            continue
        rel = p.relative_to(app_dir)
        if rel.parts[0] in _VERSION_EXCLUDED_DIRS:
            continue
        raw = p.read_bytes()
        if len(raw) > _APP_FILE_DB_MAX_BYTES:
            skipped.append(rel.as_posix())
            continue
        files.append((rel.as_posix(), raw))
    return files, skipped


def _app_file_objs(workflow_key: str, files: list, version: int, wf_version: int, editor: Optional[str] = None) -> list:
    from app.models.standard.agent import AgentAppFile

    return [
        AgentAppFile(workflow_key=workflow_key, path=rel, content=raw, size=len(raw), version=version, wf_version=wf_version, editor=editor)
        for rel, raw in files
    ]


async def _next_archive_version(workflow_key: str) -> int:
    from app.models.standard.agent import AgentAppFile

    versions = set(await AgentAppFile.filter(workflow_key=workflow_key, version__gte=1).values_list("version", flat=True))
    return (max(versions) + 1) if versions else 1


async def _drop_old_archives(workflow_key: str) -> None:
    """保留线：版本存档只留最近 _MAX_APP_VERSIONS 版，更旧的整版删除。"""
    from app.models.standard.agent import AgentAppFile

    versions = sorted(
        set(await AgentAppFile.filter(workflow_key=workflow_key, version__gte=1).values_list("version", flat=True)),
        reverse=True,
    )
    drop = versions[_MAX_APP_VERSIONS:]
    if drop:
        await AgentAppFile.filter(workflow_key=workflow_key, version__in=drop).delete()


async def _archive_files(workflow_key: str, wf_version: int, files: list, editor: Optional[str] = None) -> int:
    """把一组文件作为新存档版本写入 DB，返回存档版本号。"""
    from app.models.standard.agent import AgentAppFile

    n = await _next_archive_version(workflow_key)
    await AgentAppFile.bulk_create(_app_file_objs(workflow_key, files, n, wf_version, editor), batch_size=32)
    await _drop_old_archives(workflow_key)
    return n


async def db_save_app_state(workflow_key: str, wf_version: int, app_dir: Path, editor: Optional[str] = "human") -> Optional[dict]:
    """用户「发布」固化版本时调用：目录全量 → 新存档版本 + 先清后写刷新活动集。返回存档信息；无入口文件返回 None。

    超限文件被跳过并列在 skipped 里（调用方提示用户：它们只在发布机器上可见）。"""
    files, skipped = await asyncio.to_thread(_scan_app_files, app_dir)
    if not any(rel == "index.html" for rel, _ in files):
        return None
    from app.models.standard.agent import AgentAppFile

    n = await _archive_files(workflow_key, wf_version, files, editor)
    await AgentAppFile.filter(workflow_key=workflow_key, version=_LIVE_VERSION).delete()
    await AgentAppFile.bulk_create(_app_file_objs(workflow_key, files, _LIVE_VERSION, 0), batch_size=32)
    return {"version": n, "fileCount": len(files), "totalBytes": sum(len(raw) for _, raw in files), "skipped": skipped}


async def db_sync_live_from_disk(workflow_key: str, app_dir: Path) -> Optional[dict]:
    """agent 发布（publish_html_board）时调用：目录全量 → 只刷新活动集，**不产生存档**。

    版本语义改造后 agent 发布 = 「更新用户画面」，存档只在用户点「发布」时固化。
    返回文件统计；无入口文件返回 None。"""
    files, skipped = await asyncio.to_thread(_scan_app_files, app_dir)
    if not any(rel == "index.html" for rel, _ in files):
        return None
    from app.models.standard.agent import AgentAppFile

    await AgentAppFile.filter(workflow_key=workflow_key, version=_LIVE_VERSION).delete()
    await AgentAppFile.bulk_create(_app_file_objs(workflow_key, files, _LIVE_VERSION, 0), batch_size=32)
    return {"fileCount": len(files), "totalBytes": sum(len(raw) for _, raw in files), "skipped": skipped}


async def db_snapshot_live(workflow_key: str, wf_version: int, editor: Optional[str] = None) -> Optional[int]:
    """纯 DB 存档当前态（本机目录为空时，如跨机切换版本）：活动集 → 新存档版本。返回版本号；活动集为空返回 None。"""
    from app.models.standard.agent import AgentAppFile

    rows = await AgentAppFile.filter(workflow_key=workflow_key, version=_LIVE_VERSION)
    if not rows:
        return None
    files = [(r.path, bytes(r.content)) for r in rows]
    return await _archive_files(workflow_key, wf_version, files, editor)


# ── 内容指纹：版本去重（发布时内容没变不重复建版）与切换前自动备份判定（与所有存档都不同才备份）──


def _manifest_hash(pairs: list) -> str:
    """[(path, 内容md5hex)] → 单一指纹：按路径排序后拼 md5。路径与内容全同才算同版。"""
    import hashlib

    h = hashlib.md5()
    for rel, fh in sorted(pairs):
        h.update(f"{rel}:{fh}\n".encode("utf-8"))
    return h.hexdigest()


def _scan_app_hashes(app_dir: Path) -> tuple:
    """扫描应用目录取可入库文件的指纹，返回 ([(相对路径, md5hex)], [超限被跳过的相对路径])。
    与 _scan_app_files 同口径（同排除规则、同大小上限），保证磁盘指纹与 DB 存档指纹可比。"""
    import hashlib

    pairs: list = []
    skipped: list = []
    if not app_dir.is_dir():
        return pairs, skipped
    for p in sorted(app_dir.rglob("*")):
        if not p.is_file() or p.name.endswith(".tmp"):
            continue
        rel = p.relative_to(app_dir)
        if rel.parts[0] in _VERSION_EXCLUDED_DIRS:
            continue
        if p.stat().st_size > _APP_FILE_DB_MAX_BYTES:
            skipped.append(rel.as_posix())
            continue
        pairs.append((rel.as_posix(), hashlib.md5(p.read_bytes()).hexdigest()))
    return pairs, skipped


async def _db_manifest_hashes(workflow_key: str, version: Optional[int] = None) -> dict:
    """DB 侧算存档/活动集指纹（SQL MD5，BLOB 内容不过线）：{version: 指纹}。
    version=None 取全部存档（>=1）；传 0 取活动集。"""
    from tortoise import Tortoise

    conn = Tortoise.get_connection("conn_standard")
    if version is None:
        cond, params = "version >= 1", [workflow_key]
    else:
        cond, params = "version = %s", [workflow_key, version]
    _, rows = await conn.execute_query(
        f"SELECT version, path, MD5(content) AS h FROM agent_app_file WHERE workflow_key = %s AND {cond}",
        params,
    )
    acc: dict = {}
    for r in rows:
        acc.setdefault(int(r["version"]), []).append((str(r["path"]), str(r["h"])))
    return {v: _manifest_hash(pairs) for v, pairs in acc.items()}


async def db_upsert_live_file(workflow_key: str, rel: str, raw: bytes) -> None:
    """/save 写回双写：DB 活动集里更新对应文件（用户数据的跨机一致真相源）。"""
    from app.models.standard.agent import AgentAppFile

    await AgentAppFile.filter(workflow_key=workflow_key, path=rel, version=_LIVE_VERSION).delete()
    await AgentAppFile.create(workflow_key=workflow_key, path=rel, content=raw, size=len(raw), version=_LIVE_VERSION, wf_version=0)


def _write_materialized(base: Path, rows: list) -> None:
    """把 DB 文件行写进磁盘（物化）。路径防御：拒绝任何逃逸形态；单文件原子替换防读到半截。"""
    for r in rows:
        rel = str(r.path or "")
        parts = rel.split("/")
        if not rel or rel.startswith("/") or ".." in parts or "." in parts or "\\" in rel:
            continue
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(bytes(r.content))
        os.replace(tmp, target)


async def ensure_app_files(user_id: Any, workflow_key: str) -> int:
    """惰性物化：本机目录缺入口而 DB 有活动集时，把文件从 DB 捞出来落盘。返回物化文件数。

    请求侧消费文件前调用（qa.py 消息入口 / html_app 静态托管与 extract），备好目录再放行。
    只在 index.html 缺失时触发：本机已存在的开发目录不会被 DB 覆盖。"""
    from app.models.standard.agent import AgentAppFile

    base = _app_dir(user_id, workflow_key)
    if await asyncio.to_thread((base / "index.html").is_file):
        return 0
    rows = await AgentAppFile.filter(workflow_key=workflow_key, version=_LIVE_VERSION)
    if not rows:
        return 0
    base.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_write_materialized, base, rows)
    return len(rows)


def _read_version_meta(ver_dir: Path) -> dict:
    meta = {}
    try:
        meta = json.loads((ver_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return meta if isinstance(meta, dict) else {}


async def list_app_versions_db(wf: AgentWorkflow) -> list:
    """版本存档清单（版本号降序）：[{version, archivedAt(ms), editor, fileCount, totalBytes, current}]。

    DB 侧 GROUP BY 聚合（与节点板清单同款做法），BLOB 不过线。
    current = 与 wf.app_version 指针一致（正在查看的那版）；工作态未固化时无任何版本标当前。"""
    from tortoise import Tortoise

    conn = Tortoise.get_connection("conn_standard")
    _, rows = await conn.execute_query(
        "SELECT version, MAX(editor) AS editor, COUNT(*) AS cnt, COALESCE(SUM(size), 0) AS total, MIN(create_time) AS archived"
        " FROM agent_app_file WHERE workflow_key = %s AND version >= 1"
        " GROUP BY version ORDER BY version DESC",
        [wf.workflow_key],
    )
    out = []
    for r in rows:
        archived = r.get("archived")
        out.append({
            "version": int(r["version"]),
            "archivedAt": int(archived.timestamp() * 1000) if archived else None,
            "editor": r.get("editor"),
            "fileCount": int(r.get("cnt") or 0),
            "totalBytes": int(r.get("total") or 0),
            "current": wf.app_version is not None and int(r["version"]) == int(wf.app_version),
        })
    return out


async def rollback_app_files(wf: AgentWorkflow, user_id: Any, version: int) -> bool:
    """把应用文件切换到存档版本：目标版文件盖回磁盘 + 活动集同步 + 指针由调用方更新。

    ① 仅当前工作态与**所有**存档指纹都不同（存在未固化改动）时，才先自动备份一版防丢失——
       已固化过的状态不重复复制（版本语义改造：切换不再无脑产生新版本）；
    ② 目标版文件盖回磁盘（uploads/ 与 .versions/ 原样保留）；
    ③ 活动集改成目标版内容（其他实例物化到的也是切换后状态）。
    版本不存在 / 无入口文件返回 False。"""
    from app.models.standard.agent import AgentAppFile

    key = wf.workflow_key
    version = int(version)
    if not await AgentAppFile.filter(workflow_key=key, version=version, path="index.html").exists():
        return False
    app_dir = _app_dir(user_id, key)

    # ① 未固化改动才备份：当前态指纹（磁盘优先，跨机无目录用 DB 活动集）对比全部存档指纹
    try:
        archive_hashes = await _db_manifest_hashes(key)
        if await asyncio.to_thread((app_dir / "index.html").is_file):
            pairs, _skipped = await asyncio.to_thread(_scan_app_hashes, app_dir)
            cur_hash = _manifest_hash(pairs)
            from_disk = True
        else:
            live_hashes = await _db_manifest_hashes(key, _LIVE_VERSION)
            cur_hash = live_hashes.get(_LIVE_VERSION)
            from_disk = False
        if cur_hash and cur_hash not in archive_hashes.values():
            if from_disk:
                await db_save_app_state(key, wf.version, app_dir, editor=wf.editor or "human")
            else:
                await db_snapshot_live(key, wf.version, editor=wf.editor or "human")
    except Exception as e:  # noqa: BLE001 —— 备份是防丢增强，失败不阻塞切换本体
        logger.warning(f"[agent_workflow] 切换前自动备份失败 {key}: {e!r}")

    # ② 磁盘还原
    rows = await AgentAppFile.filter(workflow_key=key, version=version)

    def _do() -> None:
        app_dir.mkdir(parents=True, exist_ok=True)
        for child in app_dir.iterdir():
            if child.name in _VERSION_EXCLUDED_DIRS:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        _write_materialized(app_dir, rows)

    await asyncio.to_thread(_do)
    # ③ 活动集 = 目标版内容
    await AgentAppFile.filter(workflow_key=key, version=_LIVE_VERSION).delete()
    await AgentAppFile.bulk_create(_app_file_objs(key, [(r.path, bytes(r.content)) for r in rows], _LIVE_VERSION, 0), batch_size=32)
    return True


async def delete_app_files_db(workflow_key: str) -> None:
    """删任务时连带清理：DB 里该板的活动集与全部存档 + 行数据层（删板即删数据）。"""
    from app.models.standard.agent import AgentAppFile, AgentAppRow

    await AgentAppFile.filter(workflow_key=workflow_key).delete()
    await AgentAppRow.filter(workflow_key=workflow_key).delete()


async def backfill_app_files() -> None:
    """启动回填：本机已发布的应用制作把文件补进 DB（覆盖功能上线前的存量板）。

    每台实例启动时扫自己的 apps 目录：DB 没有活动集就从磁盘补；DB 没有任何存档而目录里
    还有历史 .versions/ 磁盘快照的，按版本号导入（保住既有回滚历史）。导入后磁盘存档不再读写。"""
    from app.models.standard.agent import AgentAppFile

    def _scan_local() -> list:
        """[(user_id, app_dir)]：只收已发布入口的板。"""
        out = []
        if not _USERS_ROOT.is_dir():
            return out
        for user_dir in _USERS_ROOT.iterdir():
            apps_dir = user_dir / "apps"
            if not user_dir.is_dir() or not user_dir.name.isdigit() or not apps_dir.is_dir():
                continue
            for app_dir in apps_dir.iterdir():
                if app_dir.is_dir() and (app_dir / "index.html").is_file():
                    out.append((int(user_dir.name), app_dir))
        return out

    local = await asyncio.to_thread(_scan_local)
    for user_id, app_dir in local:
        key = app_dir.name
        try:
            wf = await AgentWorkflow.get_or_none(workflow_key=key, board_type="html", is_deleted=0)
            # 1) 活动集回填：DB 没有就从本机磁盘写
            if not await AgentAppFile.filter(workflow_key=key, version=_LIVE_VERSION).exists():
                files, _skipped = await asyncio.to_thread(_scan_app_files, app_dir)
                if any(rel == "index.html" for rel, _ in files):
                    await AgentAppFile.bulk_create(_app_file_objs(key, files, _LIVE_VERSION, 0), batch_size=32)
            # 2) 历史磁盘存档导入：DB 完全没有存档时，把 .versions/v{N}/ 逐版搬进 DB
            if not await AgentAppFile.filter(workflow_key=key, version__gte=1).exists():
                ver_dirs = await asyncio.to_thread(_iter_version_dirs, app_dir)
                for vdir in ver_dirs:
                    meta = await asyncio.to_thread(_read_version_meta, vdir)

                    def _read_dir(src: Path = vdir) -> list:
                        fs: list = []
                        for p in sorted(src.rglob("*")):
                            if not p.is_file() or p.name in ("meta.json",) or p.name.endswith(".tmp"):
                                continue
                            raw = p.read_bytes()
                            if len(raw) > _APP_FILE_DB_MAX_BYTES:
                                continue
                            fs.append((p.relative_to(src).as_posix(), raw))
                        return fs

                    files = await asyncio.to_thread(_read_dir)
                    if not any(rel == "index.html" for rel, _ in files):
                        continue
                    await AgentAppFile.bulk_create(
                        _app_file_objs(key, files, int(vdir.name[1:]), int(meta.get("wfVersion") or (wf.version if wf else 0))),
                        batch_size=32,
                    )
                if ver_dirs:
                    await _drop_old_archives(key)
        except Exception as e:  # noqa: BLE001 —— 单板回填失败不阻塞启动，跳过该板
            logger.warning(f"[backfill_app_files] {key} 回填失败：{e!r}")


# ── 节点流程编排（board 型）版本存档：DB 全量快照 ──────────────────────────────
# 节点板没有「发布」动作，每次 version 递增的写入即用户可见状态——因此每次写入后追加快照。
# 保留最近 _MAX_BOARD_VERSIONS 版；回滚本身也是写入，同样走这条链路存档（回滚可再回滚）。

_MAX_BOARD_VERSIONS = 20


async def archive_wf_version(wf: AgentWorkflow) -> None:
    """节点板写入后存档当前态（version 已递增、已 save 之后调用）。html 板存档在磁盘，此处跳过。"""
    if (wf.board_type or "board") != "board":
        return
    await AgentWorkflowVersion.create(
        workflow_key=wf.workflow_key,
        version_no=wf.version,
        snapshot={
            "title": wf.title,
            "nodes": wf.nodes or [],
            "edges": wf.edges or [],
            "viewport": wf.viewport,
        },
        editor=wf.editor,
    )
    keep_ids = await AgentWorkflowVersion.filter(workflow_key=wf.workflow_key).order_by(
        "-version_no"
    ).limit(_MAX_BOARD_VERSIONS).values_list("id", flat=True)
    await AgentWorkflowVersion.filter(workflow_key=wf.workflow_key).exclude(id__in=list(keep_ids)).delete()


async def list_wf_versions(wf: AgentWorkflow, user_id: Any) -> list:
    """版本清单（两板型统一字段，版本号降序）：[{version, archivedAt(ms), editor, current, ...}]。

    html 板读磁盘 .versions/（fileCount / totalBytes）；节点板读 DB 快照（nodeCount / edgeCount）。
    user_id 显式传入（HTTP 端点 = 当前登录用户；agent 工具 = 工具工厂绑定的用户，不依赖 CTX）。"""
    if (wf.board_type or "board") == "html":
        # 存档在 DB（agent_app_file version>=1），跨机一律可读；不再依赖本机磁盘 .versions/
        return await list_app_versions_db(wf)

    # 清单只要 nodeCount / edgeCount——经 ORM 整行读回等于 版数 × 全板 JSON 过线再逐份解析
    # （20 版 ≈ 数 MB，板子一大此接口直接超时）。改 JSON_LENGTH 在 DB 侧计数，快照不过线；
    # 万一 DB 不支持 JSON 函数则回退 ORM 全量读（慢但可用）
    try:
        from tortoise import Tortoise

        conn = Tortoise.get_connection("conn_standard")
        _, rows = await conn.execute_query(
            "SELECT version_no, editor, create_time,"
            " COALESCE(JSON_LENGTH(snapshot, '$.nodes'), 0) AS node_count,"
            " COALESCE(JSON_LENGTH(snapshot, '$.edges'), 0) AS edge_count"
            " FROM agent_workflow_version WHERE workflow_key = %s ORDER BY version_no DESC",
            [wf.workflow_key],
        )
        out = []
        for r in rows:
            ct = r.get("create_time")
            out.append({
                "version": r["version_no"],
                "archivedAt": int(ct.timestamp() * 1000) if ct else None,
                "editor": r.get("editor"),
                "nodeCount": int(r.get("node_count") or 0),
                "edgeCount": int(r.get("edge_count") or 0),
                "current": r["version_no"] == wf.version,
            })
        return out
    except Exception:  # noqa: BLE001 —— JSON 函数不可用等异常回退全量读
        pass
    rows = await AgentWorkflowVersion.filter(workflow_key=wf.workflow_key).order_by("-version_no")
    out = []
    for r in rows:
        snap = r.snapshot or {}
        out.append({
            "version": r.version_no,
            "archivedAt": int(r.create_time.timestamp() * 1000) if r.create_time else None,
            "editor": r.editor,
            "nodeCount": len(snap.get("nodes") or []),
            "edgeCount": len(snap.get("edges") or []),
            "current": r.version_no == wf.version,
        })
    return out


async def rollback_wf_to_version(wf: AgentWorkflow, version: int, editor: str, user_id: Any) -> Optional[int]:
    """切换工作流到指定存档版本。返回新的 wf.version；版本不存在 / 损坏返回 None。

    html 板：未固化改动先自动备份，再时点还原磁盘文件，app_version 指针改指目标版；
    节点板：先存档当前 DB 态再覆盖字段。
    调用方拿到非 None 即已成功，前端应重新拉取工作流刷新画布。user_id 显式传入（同 list_wf_versions）。"""
    if (wf.board_type or "board") == "html":
        # DB 真相源切换：未固化改动才备份 → 目标版盖回磁盘 → 活动集同步（跨机一致）
        if not await rollback_app_files(wf, user_id, version):
            return None
        wf.version += 1
        wf.editor = editor
        wf.app_version = int(version)  # 指针改指：当前查看的就是这个存档版本
        wf.entry_ready = 1  # 存档必有 index.html，入口就绪只升不降
        try:
            wf.html_preview = await asyncio.to_thread(extract_html_preview, _app_dir(user_id, wf.workflow_key))
        except Exception:  # noqa: BLE001 —— 预览提取失败不阻塞切换
            pass
        await wf.save()
        return wf.version

    row = await AgentWorkflowVersion.get_or_none(workflow_key=wf.workflow_key, version_no=version)
    if row is None or not isinstance(row.snapshot, dict) or not row.snapshot:
        return None
    snap = row.snapshot
    wf.title = str(snap.get("title") or wf.title)[:128]
    wf.nodes = snap.get("nodes") or []
    wf.edges = snap.get("edges") or []
    wf.viewport = snap.get("viewport")
    wf.version += 1
    wf.editor = editor
    wf.human_edit = None  # 回滚后旧改动简报已无意义
    wf.marks = None       # 徽标是上一轮协作态信号，整板替换后清零
    await wf.save()
    await archive_wf_version(wf)  # 回滚后的状态同样进存档（可再回滚）
    return wf.version


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    title: str = Field("未命名工作流", alias="title")
    session_key: Optional[str] = Field(None, alias="sessionKey")
    # 板型：board 流程编排（默认）/ html 应用制作
    board_type: str = Field("board", alias="boardType")
    nodes: Optional[List[Any]] = None
    edges: Optional[List[Any]] = None
    viewport: Optional[dict] = None

    class Config:
        populate_by_name = True


class WorkflowUpdate(BaseModel):
    title: Optional[str] = None
    nodes: Optional[List[Any]] = None
    edges: Optional[List[Any]] = None
    viewport: Optional[dict] = None
    # 人本次改动的简报（前端本地 diff 后带上），供 Agent 下轮读板时感知人的改动
    human_edit: Optional[dict] = Field(None, alias="humanEdit")
    # 节点徽标（临时协作态）：前端维护人编辑标，随保存原样回传；agent 端写入时全量重建
    marks: Optional[dict] = None

    class Config:
        populate_by_name = True


class NodesPatch(BaseModel):
    nodes: List[Any]
    human_edit: Optional[dict] = Field(None, alias="humanEdit")

    class Config:
        populate_by_name = True


class WorkflowShare(BaseModel):
    # 应用制作分享开关：on=true 开启（需已发布过 index.html）；false 关闭（已签发 token 1 天内仍有效，
    # 但 share-view 端点不再签发新 token，关板后重新分享需再开）
    on: bool
    # 分享模式（仅 on=true 时有意义）：true=免登录公开，任意访客匿名打开；false=仅登录用户可打开。
    # 不传按 true（免登录）——开启分享的默认打开方式
    public_: bool = Field(True, alias="public")

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gen_key() -> str:
    return "wf_" + secrets.token_hex(12)


async def _get_owned(workflow_key: str) -> Optional[AgentWorkflow]:
    """按 workflow_key 取当前用户拥有的未删除工作流。"""
    uid = CTX_USER_ID.get()
    return await AgentWorkflow.get_or_none(
        workflow_key=workflow_key, user_id=uid, is_deleted=0
    )


async def _to_dict(wf: AgentWorkflow) -> dict:
    d = await wf.to_dict()
    d["workflowKey"] = wf.workflow_key
    d["sessionKey"] = wf.session_key
    d["boardType"] = wf.board_type or "board"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", summary="创建工作流")
async def create_workflow(
        body: WorkflowCreate,
        _auth: AuthControl = Depends(),
):
    uid = CTX_USER_ID.get()
    board_type = body.board_type or "board"
    if board_type not in _BOARD_TYPES:
        return Fail(code="4000", msg="未知板型")
    wf = await AgentWorkflow.create(
        workflow_key=_gen_key(),
        session_key=body.session_key,
        user_id=uid,
        title=body.title[:128],
        board_type=board_type,
        nodes=body.nodes or [],
        edges=body.edges or [],
        viewport=body.viewport,
        version=1,
        editor="human",
    )
    # 应用制作：预建应用目录（agent 文件工具与托管路由的共同落点）
    if board_type == "html":
        await asyncio.to_thread(_app_dir(uid, wf.workflow_key).mkdir, parents=True, exist_ok=True)
    return Success(data=await _to_dict(wf), msg="创建成功")


@router.get("/list", summary="列出当前用户的工作流")
async def list_workflows(
        keyword: Optional[str] = None,
        _auth: AuthControl = Depends(),
):
    uid = CTX_USER_ID.get()
    # 空板兜底清扫：0 节点且超过 20 分钟无更新的板软删掉，且不出现在本次返回列表里（查出后就地判定，
    # 删除与过滤同请求完成，不存在"删慢了列表还能看到"的时序问题）。
    # 前端只在 goBack / 组件卸载时即时清理，关标签页、离线等路径会漏；放在列表请求里兜底必然清到。
    # 20 分钟宽限：保护刚建好、agent 还没来得及播种的板（用户可能正在板子页等首条回复）。
    # 豁免 html 看板：它的 nodes 恒为空，"有没有内容"看的是目录里的 index.html（前端 goBack/unmount
    # 用 entryReady 即时清理；列表级文件判空成本高，v1 直接豁免）。
    # 单查询 + 列投影：列表只消费下列字段，human_edit（改动简报可很大）/ marks / viewport 一律不读；
    # 空板清扫也不再单独跑一遍全 nodes 查询，直接并入本次结果集判定
    qs = AgentWorkflow.filter(user_id=uid, is_deleted=0).only(
        "id", "workflow_key", "title", "board_type", "version", "update_time", "html_preview", "share_on", "share_public", "nodes", "edges"
    )
    if keyword:
        qs = qs.filter(title__icontains=keyword)
    rows = await qs.order_by("-update_time")

    cutoff = tz.now() - timedelta(minutes=20)
    stale_ids = [
        r.id
        for r in rows
        if r.update_time and r.update_time < cutoff
        and not (r.nodes or []) and (r.board_type or "board") != "html"
    ]
    if stale_ids:
        await AgentWorkflow.filter(id__in=stale_ids).update(is_deleted=1)
        stale_set = set(stale_ids)
        rows = [r for r in rows if r.id not in stale_set]

    # 对话过的板置顶：agent_session 按 workflow_key 归集每板最近对话时间（会话只在发消息时创建，
    # 有会话 = 用户在该板内对话过）。先全量排序再截断——按 update_time 截前 50 会把久未编辑但有对话的老板漏掉
    chat_time: dict[str, Any] = {}
    wf_keys = [r.workflow_key for r in rows]
    if wf_keys:
        sessions = await AgentSession.filter(user_id=uid, workflow_key__in=wf_keys, is_deleted=0).only("workflow_key", "update_time")
        for s in sessions:
            if not s.workflow_key or not s.update_time:
                continue
            prev = chat_time.get(s.workflow_key)
            if prev is None or s.update_time > prev:
                chat_time[s.workflow_key] = s.update_time

    def _sort_key(r):
        ct = chat_time.get(r.workflow_key)
        if ct is not None:
            return (0, -ct.timestamp())  # 对话过的在前，组内按最近对话时间降序
        return (1, -(r.update_time.timestamp() if r.update_time else 0))  # 未对话的按更新时间降序

    rows = sorted(rows, key=_sort_key)[:50]

    def _ms(v) -> Optional[int]:
        return int(v.timestamp() * 1000) if v else None

    def _node_label(n: dict) -> str:
        """列表卡片预览用的节点短标签（按节点类型取字段）。"""
        nd = n.get("data") or {}
        ntype = str(n.get("type") or "textNode")
        if ntype == "fileNode":
            return f"📎 {nd.get('name') or '附件'}"
        if ntype == "segNode":  # 分镜段卡（一卡一场戏）
            head = " ".join(x for x in [str(nd.get("seg") or ""), str(nd.get("duration") or "")] if x)
            shots = nd.get("shots")
            shots_s = f"（分镜 {len(shots)}）" if isinstance(shots, list) and shots else ""
            return f"🎬 {head or '分镜段'}{shots_s}"
        if ntype == "reviewNode":
            return f"✋ {nd.get('question') or '人工核查'}"
        if ntype == "taskNode":
            return str(nd.get("title") or "工作项")
        if ntype == "dataNode":
            return str(nd.get("title") or "数据")
        if ntype == "conclusionNode":
            return f"◆ {nd.get('claim') or '结论'}"
        if ntype in ("startNode", "endNode"):
            return str(nd.get("label") or ("开始" if ntype == "startNode" else "结束"))
        return str(nd.get("text") or "未命名")

    # 应用制作预览素材兜底回填：功能上线前发布的板 html_preview 为空，本机目录恰好有 index.html 就就地提取并落库
    # （跨机读不到文件的实例跳过，等发布动作或文件所在实例补上；尽力而为，失败不影响列表）
    for r in rows:
        if (r.board_type or "board") != "html" or r.html_preview:
            continue
        app_dir = _app_dir(uid, r.workflow_key)
        if not await asyncio.to_thread((app_dir / "index.html").exists):
            continue
        pv = await asyncio.to_thread(extract_html_preview, app_dir)
        if pv:
            r.html_preview = pv
            await r.save(update_fields=["html_preview"])

    data = []
    for r in rows:
        node_list = r.nodes or []
        # 前 4 个节点的精简预览（供列表卡片画迷你流程），label 截断防载荷膨胀
        preview = [
            {
                "label": _node_label(n)[:20],
                "type": str(n.get("type") or "textNode"),
            }
            for n in node_list[:4]
        ]
        data.append({
            "workflowKey": r.workflow_key,
            "title": r.title,
            "boardType": r.board_type or "board",
            "version": r.version,
            "updateTime": _ms(r.update_time),
            # 最近一次在本任务内对话的时间（null=从未对话）；前端据此打「对话过」徽标，排序已在后端置顶
            "lastChatTime": _ms(chat_time.get(r.workflow_key)),
            "nodeCount": len(node_list),
            "edgeCount": len(r.edges or []),
            "preview": preview,
            # 应用制作列表卡预览素材（title/tagline/accent），board 型恒为 null
            "htmlPreview": r.html_preview if (r.board_type or "board") == "html" else None,
            # 分享开关与模式（仅 html 型有意义），前端顶栏分享按钮据此显示当前状态
            "shareOn": bool(getattr(r, "share_on", 0)) if (r.board_type or "board") == "html" else False,
            "sharePublic": bool(getattr(r, "share_public", 0)) if (r.board_type or "board") == "html" else False,
        })
    return Success(data=data)


# 全量读缓存（workflow_key → (version, ts, data)）：nodes / edges 大 JSON 列的读取是本接口最慢
# 的一环（大板 1-3s），而 version 在任何写入（人保存 / agent 改板 / 回滚）时必递增——先列投影查
# version 对缓存，命中即返回，同一块未变更板子的重复全量读（打开页面、轮询发现变更后拉全量、
# 多标签页）从秒级降到毫秒级，也免掉大 JSON 列反复过线。
# 只缓存 board 型全量读：html 板有 entryReady 本机文件判定；node_ids 部分读场景各异。
_BOARD_READ_CACHE: dict = {}
_BOARD_READ_CACHE_MAX = 32
_BOARD_READ_CACHE_TTL = 600  # 兜底强制过期，防极端情况下版本号回退等造成脏读


def _board_cache_get(workflow_key: str, version: int) -> Optional[dict]:
    hit = _BOARD_READ_CACHE.get(workflow_key)
    if hit and hit[0] == version and time.time() - hit[1] < _BOARD_READ_CACHE_TTL:
        return hit[2]
    return None


def _board_cache_put(workflow_key: str, version: int, data: dict) -> None:
    _BOARD_READ_CACHE[workflow_key] = (version, time.time(), data)
    if len(_BOARD_READ_CACHE) > _BOARD_READ_CACHE_MAX:
        oldest = min(_BOARD_READ_CACHE, key=lambda k: _BOARD_READ_CACHE[k][1])
        del _BOARD_READ_CACHE[oldest]


@router.get("/{workflow_key}", summary="读取工作流")
async def get_workflow(
        workflow_key: str,
        node_ids: Optional[str] = None,
        _auth: AuthControl = Depends(),
):
    uid = CTX_USER_ID.get()
    # 全量读快路径：列投影只取 version / board_type，同版缓存命中直接返回，不读大 JSON 列
    if not node_ids:
        light = await AgentWorkflow.filter(
            workflow_key=workflow_key, user_id=uid, is_deleted=0
        ).only("version", "board_type").first()
        if not light:
            return Fail(code="4004", msg="工作流不存在")
        if (light.board_type or "board") == "board":
            cached = _board_cache_get(workflow_key, light.version)
            if cached is not None:
                return Success(data=cached)

    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    d = await _to_dict(wf)

    # 应用制作：附带入口就绪状态（index.html 是否已发布），前端画布据此决定渲染 iframe 还是占位；
    # 空任务清理也以此为准——必须走 DB 标志 + 本机文件双判（见 _resolve_entry_ready）
    if (wf.board_type or "board") == "html":
        uid = CTX_USER_ID.get()
        d["entryReady"] = await _resolve_entry_ready(wf, uid, workflow_key)
        d["shareOn"] = bool(wf.share_on)
        d["sharePublic"] = bool(wf.share_public)

    # 部分读取：?node_ids=1,2,3
    if node_ids and wf.nodes:
        wanted = set(node_ids.split(","))
        d["nodes"] = [n for n in wf.nodes if str(n.get("id")) in wanted]
        # 附带关联边
        if wf.edges:
            d["edges"] = [
                e for e in wf.edges
                if str(e.get("source")) in wanted or str(e.get("target")) in wanted
            ]
    elif (wf.board_type or "board") == "board":
        # 全量 board 读入缓存（见 _BOARD_READ_CACHE 说明）；node_ids 分支的 d 已被裁剪，不缓存
        _board_cache_put(workflow_key, wf.version, d)

    return Success(data=d)


@router.get("/{workflow_key}/meta", summary="工作流轻量元信息（高频轮询专用）")
async def get_workflow_meta(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    """只回 version / title / boardType / entryReady，列投影、不读 nodes / edges。

    前端每 3.5s 轮询跟随 Agent 改动：板子大了之后全量读一次要搬整板 JSON，轮询变成持续重负载。
    改轮询此端点，仅当 version 变化才调全量读接口。entryReady 只认 DB 标志（发布时落库的跨机真相源），
    不在轮询里探本机文件，保持纯 DB 单查询。"""
    uid = CTX_USER_ID.get()
    wf = await AgentWorkflow.filter(
        workflow_key=workflow_key, user_id=uid, is_deleted=0
    ).only("version", "title", "board_type", "entry_ready").first()
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    return Success(data={
        "version": wf.version,
        "title": wf.title,
        "boardType": wf.board_type or "board",
        "entryReady": bool(wf.entry_ready),
    })


@router.put("/{workflow_key}", summary="整体更新工作流")
async def update_workflow(
        workflow_key: str,
        body: WorkflowUpdate,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    payload = body.model_dump(exclude_unset=True, by_alias=False)
    human_edit = payload.pop("human_edit", None)
    if payload or human_edit is not None:
        if payload:
            await wf.update_from_dict(payload).save()
        wf.editor = "human"
        if human_edit is not None:
            wf.human_edit = human_edit
        wf.version += 1
        await wf.save()
        await archive_wf_version(wf)  # 每次 version 递增的写入都存档（回滚的真相源）

    return Success(data=await _to_dict(wf), msg="更新成功")


@router.patch("/{workflow_key}/nodes", summary="部分合并节点（按 ID）")
async def patch_nodes(
        workflow_key: str,
        body: NodesPatch,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    existing: list = wf.nodes or []
    idx = {str(n.get("id")): i for i, n in enumerate(existing)}

    for incoming in body.nodes:
        nid = str(incoming.get("id", ""))
        if not nid:
            continue
        if nid in idx:
            # 深度合并 data 字段，其余字段覆盖
            old = existing[idx[nid]]
            for k, v in incoming.items():
                if k == "data" and isinstance(v, dict) and isinstance(old.get("data"), dict):
                    old["data"].update(v)
                else:
                    old[k] = v
        else:
            existing.append(incoming)

    wf.nodes = existing
    wf.editor = "human"
    if body.human_edit is not None:
        wf.human_edit = body.human_edit
    wf.version += 1
    await wf.save()
    await archive_wf_version(wf)  # 每次 version 递增的写入都存档（回滚的真相源）

    return Success(data=await _to_dict(wf), msg="节点已合并")


@router.delete("/{workflow_key}", summary="删除工作流（软删）")
async def delete_workflow(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    wf.is_deleted = 1
    await wf.save()
    # 应用制作：连带物理删除应用目录与 DB 文件（DB 软删为准，rmtree 失败不回滚）。
    # 目录消失后，已签发的托管 token 也自然全部 404（无状态 token 的撤销兜底）；
    # DB 文件一并清掉，物化才不会把已删任务复活到别的机器上。
    if (wf.board_type or "board") == "html":
        uid = CTX_USER_ID.get()
        await asyncio.to_thread(shutil.rmtree, _app_dir(uid, workflow_key), True)
        await delete_app_files_db(workflow_key)
    return Success(msg="已删除")


@router.post("/{workflow_key}/html-token", summary="签发应用制作托管 token")
async def sign_html_token(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    """签发短期托管 token：iframe 以 /ai/html-app/{token}/index.html 为 src，
    页面内相对引用的 css/js/json 与 save 写回都走同一 token 前缀（iframe 无法带 Authorization 头）。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    if (wf.board_type or "board") != "html":
        return Fail(code="4000", msg="非应用制作任务")

    uid = CTX_USER_ID.get()
    ttl_days = 7
    token = create_html_app_token(int(uid), workflow_key, ttl_days=ttl_days)
    entry_ready = await _resolve_entry_ready(wf, uid, workflow_key)
    return Success(data={"token": token, "entryReady": entry_ready, "expiresIn": ttl_days * 86400})


@router.put("/{workflow_key}/share", summary="应用制作分享开关（仅板主）")
async def set_workflow_share(
        workflow_key: str,
        body: WorkflowShare,
        _auth: AuthControl = Depends(),
):
    """开启 / 关闭应用制作的分享。开启前提：html 板且已发布过（entry_ready）。
    开启后访客可经 /share/{workflow_key} 打开使用（数据层写回 / 页内 AI 照常，不能编辑板本身）。
    模式由 body.public 决定：true=免登录公开；false=仅登录用户可打开（匿名访客由前端引导去登录）。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    if (wf.board_type or "board") != "html":
        return Fail(code="4000", msg="仅应用制作支持分享")
    if body.on:
        uid = CTX_USER_ID.get()
        if not await _resolve_entry_ready(wf, uid, workflow_key):
            return Fail(code="4000", msg="看板尚未发布，制作完成后再分享")
    wf.share_on = 1 if body.on else 0
    wf.share_public = 1 if (body.on and body.public_) else 0
    await wf.save(update_fields=["share_on", "share_public"])
    return Success(
        data={"shareOn": bool(wf.share_on), "sharePublic": bool(wf.share_public)},
        msg="分享已开启" if body.on else "分享已关闭",
    )


# 分享 token 短时效：无状态 token 无法即时吊销，关闭分享后旧 token 在 TTL 内仍可访问——
# 用 1 天 TTL 收窄该窗口（访客每次打开分享页都会重新签发，短 TTL 无感）
_SHARE_TOKEN_TTL_DAYS = 1


@public_router.get("/{workflow_key}/share-view", summary="访客打开分享的应用制作（双模式：免登录 / 仅登录）")
async def share_view(
        workflow_key: str,
        request: Request,
):
    """访客经分享链接打开看板：校验分享开启 + 已发布后，签发带 share 声明的托管 token。
    share token 与板主 token 同一目录读写（数据层 / AI 通道照常），serve 时不注入「编辑文字」脚本。

    双模式（share_public 列）：
    - 免登录公开：直接签发 token（能力 URL 语义，workflow_key 本身不保密，凭据是短期 token），
      访客保持匿名（token 不带 visitorId，行数据通道 / whoami 按匿名语义处理）。
    - 仅登录用户：需要有效 accessToken——匿名 / 无效 token 返回 4000 + needLogin 标记
      （前端据此带 redirect 引导去登录页）；token 过期透传 4010（axios 拦截器静默刷新并重试，
      绝不能塌缩成 needLogin，否则登录态临期的用户在分享页与登录页之间无限反弹）。
      校验通过后把访客 uid 嵌进 share token（visitorId claim），行数据通道与 whoami 据此识别身份。
    校验失败一律不用 4001/4002/4003 鉴权契约码（那是登出信号）。"""
    wf = await AgentWorkflow.get_or_none(workflow_key=workflow_key, is_deleted=0)
    if not wf or (wf.board_type or "board") != "html" or not wf.share_on or not wf.entry_ready:
        return Fail(code="4000", msg="分享链接无效：看板不存在或分享已关闭")

    visitor_id: int | None = None
    if not wf.share_public:
        auth = request.headers.get("authorization") or ""
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not bearer:
            return Fail(code="4000", msg="该分享需要登录后打开", data={"needLogin": True})
        status, code, decode_data = check_token(bearer)
        if not status:
            if code == Code.TOKEN_EXPIRED:
                return Fail(code=Code.TOKEN_EXPIRED, msg="登录已过期")
            return Fail(code="4000", msg="该分享需要登录后打开", data={"needLogin": True})
        claims = decode_data["data"]
        visitor = await User.get_or_none(id=claims.get("userId")) if claims.get("tokenType") == "accessToken" else None
        if not visitor:
            return Fail(code="4000", msg="该分享需要登录后打开", data={"needLogin": True})
        if visitor.status_type == StatusType.disable:
            # 刻意不带 needLogin：账号被禁用去登录页也解决不了，避免分享页与登录页弹跳循环
            return Fail(code="4000", msg="账号已被禁用，无法打开该分享")
        visitor_id = int(visitor.id)

    token = create_html_app_token(int(wf.user_id), workflow_key, ttl_days=_SHARE_TOKEN_TTL_DAYS, share=True, visitor_id=visitor_id)
    return Success(data={
        "token": token,
        "title": wf.title,
        "expiresIn": _SHARE_TOKEN_TTL_DAYS * 86400,
    })


@router.get("/{workflow_key}/versions", summary="工作流版本存档清单")
async def list_versions(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    """版本存档清单（降序，最新在前）。html 板 = 用户点「发布」固化的版本（agent_app_file），
    节点板 = 每次写入的 DB 全量快照；current=true 的版本对应当前正在查看的状态。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    return Success(data=await list_wf_versions(wf, CTX_USER_ID.get()))


@router.post("/{workflow_key}/versions/{version}/rollback", summary="切换工作流到指定存档版本")
async def rollback_version(
        workflow_key: str,
        version: int,
        _auth: AuthControl = Depends(),
):
    """时点全量还原：html 板恢复该版本的应用文件（uploads/ 原样保留）并把 app_version 指针改指，
    节点板恢复全量节点/连线。html 板仅在当前态存在未固化改动时自动备份一版，切换本身不复制新版本。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")

    new_version = await rollback_wf_to_version(wf, version, editor="human", user_id=CTX_USER_ID.get())
    if new_version is None:
        return Fail(code="4000", msg="该存档版本不存在或已损坏")
    return Success(data={"version": version, "newVersion": new_version}, msg="已切换")


@router.post("/{workflow_key}/publish", summary="应用制作发布：把当前状态固化为新版本")
async def publish_version(
        workflow_key: str,
        _auth: AuthControl = Depends(),
):
    """用户显式发布：当前应用目录固化为新存档版本（版本只在这里产生，agent 更新画面不产版本）。

    内容与既有存档完全相同则不重复建版（返回 unchanged + 既有版本号）。
    仅 html 板可用；尚未发布过入口（无 index.html）返回 4000。"""
    wf = await _get_owned(workflow_key)
    if not wf:
        return Fail(code="4004", msg="工作流不存在")
    if (wf.board_type or "board") != "html":
        return Fail(code="4000", msg="仅应用制作支持发布版本")

    uid = CTX_USER_ID.get()
    app_dir = _app_dir(uid, workflow_key)
    # 跨机兜底：本机没有文件而 DB 有活动集 → 先物化回磁盘再发布
    if not await asyncio.to_thread((app_dir / "index.html").is_file):
        try:
            await ensure_app_files(uid, workflow_key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agent_workflow] 发布前物化失败 {workflow_key}: {e!r}")
    if not await asyncio.to_thread((app_dir / "index.html").is_file):
        return Fail(code="4000", msg="还没有可发布的内容（等 Agent 先开发出应用）")

    # 版本去重：当前目录指纹与任一既有存档完全相同 → 不重复建版
    try:
        pairs, _skipped = await asyncio.to_thread(_scan_app_hashes, app_dir)
        cur_hash = _manifest_hash(pairs)
        archive_hashes = await _db_manifest_hashes(workflow_key)
        same = sorted(v for v, h in archive_hashes.items() if h == cur_hash)
        if same:
            newest = same[-1]
            if wf.app_version != newest:
                wf.app_version = newest  # 内容一致也顺便把指针指上（此前在看别的版/工作态）
                wf.editor = "human"
                await wf.save()
            return Success(data={"version": newest, "unchanged": True}, msg=f"内容与版本 {newest} 相同，未重复建版")
    except Exception as e:  # noqa: BLE001 —— 指纹比对失败不阻塞发布，照常建新版
        logger.warning(f"[agent_workflow] 发布指纹比对失败 {workflow_key}: {e!r}")

    try:
        info = await db_save_app_state(workflow_key, wf.version, app_dir, editor="human")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[agent_workflow] 发布入库失败 {workflow_key}: {e!r}")
        return Fail(code="4000", msg="发布失败，请稍后重试")
    if info is None:
        return Fail(code="4000", msg="还没有可发布的内容（缺少入口 index.html）")

    wf.app_version = info["version"]
    wf.editor = "human"
    wf.entry_ready = 1
    await wf.save()
    data = {"version": info["version"], "unchanged": False}
    if info.get("skipped"):
        data["skipped"] = info["skipped"]
    return Success(data=data, msg=f"已发布为版本 {info['version']}")
