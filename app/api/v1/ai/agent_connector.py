"""
Agent 连接器（MCP 服务连接器）API（商店模型）

- 统一可见性尺子（无 visibility 机制）：可见/可用 = is_enabled=1（已上架）OR user_id==uid（本人创建）
- 新建默认 is_enabled=0（未上架，仅创建者可见），上下架仅管理员可改（否则 4032）
- 个人偏好（agent_connector_user_pref）：is_added / is_enabled 正交，与技能偏好同口径
- 凭据模型：**添加连接器对所有人一视同仁**——表单里填的凭据永远是本人的，
  存 agent_connector_credential（一人一份、互不可见）；credential_mode 只是派生状态：
  none 无凭据 / personal 各自填写（用户添加后需填自己的凭据，未填不加载）/
  shared 带共享凭据（agent_connector.api_key，仅在管理员「带上凭据上架」时产生，全员共用）。
  特殊处理只在「上架」这一步：管理员可选是否带上凭据，默认不带（用户各自填）。
- api_key 红线：明文入库（同 agent_model_block 口径），响应只回 hasSharedKey/hasMyKey 布尔，
  /test 不回显，日志与错误文案禁止出现 key
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Optional, Union
from urllib.parse import urlparse

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.expressions import Q

from app.api.v1.ai.role_tier import norm_tier_codes, resolve_user_tier_codes, tier_allows
from app.core.ctx import CTX_USER_ID
from app.models.standard.agent import AgentConnector, AgentConnectorCredential, AgentConnectorUserPref
from app.schemas.base import Fail, Success, SuccessExtra

router = APIRouter(prefix="/agent/connectors", tags=["Agent 连接器"])

_TRANSPORTS = {"sse", "streamable_http"}
_TEST_TIMEOUT_S = 15.0


# ── Helpers ──────────────────────────────────────────────────────────────────


async def get_current_role_codes():
    """复用技能侧同款角色查询（函数级 import 避免顶层循环引用）。"""
    from app.api.v1.ai.agent_skill import get_current_role_codes as _impl

    return await _impl()


def _is_manager(is_super: bool, role_codes: list[str]) -> bool:
    from app.api.v1.ai.agent_skill import _is_manager as _impl

    return _impl(is_super, role_codes)


def _can_manage(obj, uid: Optional[int], is_manager: bool) -> bool:
    from app.api.v1.ai.agent_skill import _can_manage as _impl

    return _impl(obj, uid, is_manager)


def _listed_or_own(obj, uid: Optional[int]) -> bool:
    from app.api.v1.ai.agent_skill import _listed_or_own as _impl

    return _impl(obj, uid)


def _default_added(c, uid: Optional[int]) -> bool:
    """无偏好记录时的缺省 is_added 口径（与技能一致）：仅本人创建默认已添加。"""
    return uid is not None and c.user_id is not None and c.user_id == uid


def _validate_endpoint(transport: str, url: str) -> Optional[str]:
    """transport/url 合法性校验；返回错误文案或 None。"""
    if transport not in _TRANSPORTS:
        return f"transport 仅支持 {' / '.join(sorted(_TRANSPORTS))}"
    u = (url or "").strip()
    if not u or len(u) > 512:
        return "url 需为 1~512 字符"
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "url 仅支持 http/https 协议"
    return None


async def _derive_mode_without_shared(c: AgentConnector) -> str:
    """共享凭据不存在时的派生状态：创建者填过凭据=personal（各自填写），否则 none。"""
    if c.user_id is not None:
        row = await AgentConnectorCredential.get_or_none(user_id=c.user_id, connector_key=c.connector_key)
        if row is not None and row.api_key:
            return "personal"
    return "none"


async def _my_credential_map(uid: Optional[int]) -> dict[str, AgentConnectorCredential]:
    """当前用户的个人凭据行：{connector_key: row}。"""
    if uid is None:
        return {}
    rows = await AgentConnectorCredential.filter(user_id=uid).all()
    return {r.connector_key: r for r in rows}


def _connector_to_dict(
    c: AgentConnector,
    user_enabled: Optional[bool] = None,
    user_added: Optional[bool] = None,
    user_added_at: Optional[int] = None,
    author: Optional[str] = None,
    has_my_key: Optional[bool] = None,
) -> dict:
    """camelCase 出参；api_key 绝不回显，只给 hasSharedKey / hasMyKey 布尔。"""
    uid = CTX_USER_ID.get() or None
    created_at = int(c.create_time.timestamp() * 1000) if c.create_time else None
    added_at = user_added_at
    if added_at is None and user_added:
        added_at = created_at
    return {
        "id": c.id,
        "connectorKey": c.connector_key,
        "name": c.name,
        "description": c.description,
        "icon": c.icon,
        "kind": c.kind or "connector",
        "transport": c.transport,
        "url": c.url,
        "userId": c.user_id,
        "author": author,
        "isEnabled": bool(c.is_enabled),
        "minTierCode": getattr(c, "min_tier_code", None),
        "userEnabled": True if user_enabled is None else bool(user_enabled),
        "isAdded": _default_added(c, uid) if user_added is None else bool(user_added),
        "credentialMode": c.credential_mode,
        "hasSharedKey": bool(c.api_key),
        "hasMyKey": bool(has_my_key),
        "exampleQuestions": list(getattr(c, "example_questions", None) or []),
        "addedAt": added_at,
        "createdAt": created_at,
        "updatedAt": int(c.update_time.timestamp() * 1000) if c.update_time else None,
    }


async def _connectors_to_records(rows: list, uid: Optional[int]) -> list[dict]:
    """把本页 AgentConnector 行批量序列化为出参（偏好三 map + 个人凭据 + 作者名一次性预取）。"""
    enabled_map: dict[str, bool] = {}
    added_map: dict[str, bool] = {}
    added_at_map: dict[str, int] = {}
    keys = [c.connector_key for c in rows]
    if uid and keys:
        prefs = await AgentConnectorUserPref.filter(user_id=uid, connector_key__in=keys).all()
        enabled_map = {p.connector_key: bool(p.is_enabled) for p in prefs}
        added_map = {p.connector_key: bool(p.is_added) for p in prefs}
        added_at_map = {p.connector_key: int(p.update_time.timestamp() * 1000) for p in prefs if p.update_time}
    cred_map = await _my_credential_map(uid)
    from app.api.v1.ai.agent_skill import _author_label_map

    author_map = await _author_label_map([c.user_id for c in rows if c.user_id is not None])
    return [
        _connector_to_dict(
            c,
            enabled_map.get(c.connector_key, True),
            added_map.get(c.connector_key, _default_added(c, uid)),
            added_at_map.get(c.connector_key),
            author_map.get(c.user_id),
            has_my_key=c.connector_key in cred_map,
        )
        for c in rows
    ]


def _evict_user_agents(uid: Optional[int]) -> None:
    """连接器集变化后弹出该用户缓存的 agent 实例（签名本就会变，这里只是加速回收）。"""
    if uid is None:
        return
    try:
        # qa.py 顶层导入了 agent_skill，这里只能函数级惰性反向 import
        from app.api.v1.ai import qa as _qa

        _qa._evict_user_agents(uid)
    except Exception as e:
        logger.warning(f"[connector] agent 缓存失效失败（不影响写入）: {e}")


def _build_connection(transport: str, url: str, api_key: Optional[str]) -> dict:
    """构造 langchain-mcp-adapters 的 connection 配置（mcp_client 同款）。"""
    from app.langchain.mcp_client import build_connector_connection

    return build_connector_connection(transport, url, api_key)


def _flatten_exc(e: BaseException) -> str:
    """递归展平 ExceptionGroup / __cause__ 链，拼接全部叶子错误信息（去重保序）。"""
    parts: list[str] = []

    def _walk(x: BaseException, depth: int = 0):
        if depth > 6:
            return
        subs = getattr(x, "exceptions", None)
        if subs:
            for s in subs:
                _walk(s, depth + 1)
        else:
            parts.append(f"{type(x).__name__}: {x}")
        if x.__cause__ is not None and x.__cause__ is not x:
            _walk(x.__cause__, depth + 1)

    _walk(e)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return " | ".join(out)


def _classify_conn_error(e: BaseException, api_key: Optional[str]) -> str:
    """把试连异常翻译成可操作的文案（错误摘要里抹掉凭据，防泄漏）。"""
    text = _flatten_exc(e)
    if api_key:
        text = text.replace(api_key, "***")
    low = text.lower()
    brief = text[:180]
    if any(k in low for k in ("401", "unauthorized", "invalid api key", "invalid_api_key", "authentication")):
        return f"鉴权失败（401）：凭据无效，请检查 api_key。{brief}"
    if any(k in low for k in ("403", "forbidden")):
        return f"鉴权失败（403）：凭据无权限或已失效。{brief}"
    if any(k in low for k in ("404", "not found")):
        return f"端点不存在（404）：请检查 URL 与传输方式是否匹配（SSE 地址通常以 /sse 结尾）。{brief}"
    if "405" in low or "method not allowed" in low:
        return f"请求方式不允许（405）：传输方式可能不匹配，试试切换 SSE / Streamable HTTP。{brief}"
    if any(k in low for k in ("connection refused", "connecterror", "getaddrinfo", "name or service not known", "nodename nor servname", "network is unreachable", "connecttimeout")):
        return f"服务器无法访问：请检查地址与端口是否正确、服务是否在线。{brief}"
    return f"连接失败：{brief}"


# ── Req 模型（入参 snake_case） ──────────────────────────────────────────────

# 连接器类别白名单：connector 通用连接器 / dataset 数据集（均经 MCP 接入，底层机制一致，产品上独立维度）
_VALID_KINDS = ("connector", "dataset")


class ConnectorCreateReq(BaseModel):
    name: str = Field(..., description="显示名（1~64 字）")
    description: Optional[str] = Field(None, description="简短描述（≤1000 字）")
    transport: str = Field(..., description="sse / streamable_http")
    url: str = Field(..., description="MCP server 地址（仅 http/https）")
    kind: str = Field("connector", description="类别：connector 连接器 / dataset 数据集（均为 MCP 接入）")
    api_key: Optional[str] = Field(None, description="我的凭据（可选）：存为本人个人凭据，一人一份互不可见")
    icon: Optional[str] = Field(None, description="图标：单个 <svg> 源码或图片 data URI（可选）")
    example_questions: Optional[list[str]] = Field(None, description="快捷提问（字符串数组）：卡片展示，用户点击即添加该连接器/数据集并把问题填入输入框")


class ConnectorUpdateReq(BaseModel):
    name: Optional[str] = Field(None, description="显示名（1~64 字）")
    description: Optional[str] = Field(None, description="简短描述；空串=清除")
    transport: Optional[str] = Field(None, description="sse / streamable_http")
    url: Optional[str] = Field(None, description="MCP server 地址")
    kind: Optional[str] = Field(None, description="类别：connector / dataset；不传=不改")
    api_key: Optional[str] = Field(None, description="我的凭据：不传=不改；空串=清除；传值=替换（存本人个人凭据）")
    is_enabled: Optional[bool] = Field(None, description="商店上架/下架（仅管理员可改）")
    min_tier_code: Optional[Union[list[str], str]] = Field(
        None, description="可见档位白名单（档位 code 数组，如 ['paid','gov']）；[]/'all'=全员可见；不传=不改；显式多选无包含关系（仅管理员可改）"
    )
    icon: Optional[str] = Field(None, description="图标：单个 <svg> 源码或图片 data URI；不传=不改；空串=清除")
    shared_api_key: Optional[str] = Field(
        None,
        description="共享凭据（仅管理员）：传值=设为共享凭据（凭据方式变共享）；"
        "空串=清除共享凭据（回到各自填写）；不传=不动。与普通用户的 api_key（个人凭据）互不相干",
    )
    with_credential: Optional[str] = Field(
        None,
        description="仅上架（is_enabled=true）时有意义：mine=把我的个人凭据作为共享凭据带给所有用户；"
        "keep=保留连接器上已有的共享凭据；none/不传（默认）=不带凭据，用户各自填写",
    )
    example_questions: Optional[list[str]] = Field(None, description="快捷提问（字符串数组）；空数组=清除；不传=不改")


class ConnectorCredentialReq(BaseModel):
    api_key: str = Field(..., description="我的个人凭据；空串=清除")


class ConnectorPrefsReq(BaseModel):
    connector_keys: list[str] = Field(..., description="连接器 key 列表（≤200）")
    is_enabled: Optional[bool] = Field(None, description="个人启用/禁用")
    is_added: Optional[bool] = Field(None, description="是否已添加到我的连接器")


class ConnectorManageBatchReq(BaseModel):
    connector_keys: list[str] = Field(..., description="连接器 key 列表（≤200）")
    is_enabled: Optional[bool] = Field(None, description="批量上架/下架（全局商店可见性）")


class ConnectorKeysReq(BaseModel):
    connector_keys: list[str] = Field(..., description="连接器 key 列表（≤200）")


class ConnectorTestReq(BaseModel):
    id: Optional[int] = Field(None, description="传 id=用库里的连接器试连（其余字段忽略）")
    transport: Optional[str] = Field(None, description="保存前试连：sse / streamable_http")
    url: Optional[str] = Field(None, description="保存前试连：MCP server 地址")
    api_key: Optional[str] = Field(None, description="保存前试连：凭据（可选）")


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("", summary="连接器列表")
async def list_connectors(
    include_disabled: bool = False,
    current: Optional[int] = None,
    size: Optional[int] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    kind: Optional[str] = None,
):
    """商店模型（与技能同口径）：
    - 默认只回上架中（is_enabled=1）；include_disabled=true 附带未上架行，
      但未上架行仅对管理者（超管/管理员）或作者本人返回——上架管理视图用

    上架管理分页：管理员 + include_disabled + 同时传 current/size 时走 DB 分页，返回
    SuccessExtra({records}, total, current, size)；支持 keyword（名称/描述/url 模糊）、
    status（all/enabled/disabled）、user_id（0=官方）、kind（connector/dataset）。
    其余情形逐字保留全量逻辑（兼容既有调用方）。
    """
    from app.api.v1.ai.agent_skill import manage_paging_enabled, paging_bounds, q_author

    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)

    # kind 校验在两条分支前统一做：非法值直接 4000（禁止 4001/4010）
    k = (kind or "").strip() or None
    if k is not None and k not in _VALID_KINDS:
        return Fail(msg="类别无效：connector 或 dataset")

    # ── 分页分支（上架管理页，管理员专属） ──
    if manage_paging_enabled(is_mgr, include_disabled, current, size):
        cur, siz = paging_bounds(current, size)
        qs = AgentConnector.all()
        if k:
            qs = qs.filter(kind=k)
        kw = (keyword or "").strip()
        if kw:
            qs = qs.filter(Q(name__icontains=kw) | Q(description__icontains=kw) | Q(url__icontains=kw))
        st = (status or "all").strip() or "all"
        if st == "enabled":
            qs = qs.filter(is_enabled=1)
        elif st == "disabled":
            qs = qs.filter(is_enabled=0)
        qa = q_author(user_id)
        if qa is not None:
            qs = qs.filter(qa)
        total = await qs.count()
        # 确定性排序（跨页不重不漏）：在架优先，id 收尾
        rows = await qs.order_by("-is_enabled", "id").offset((cur - 1) * siz).limit(siz)
        records = await _connectors_to_records(rows, uid)
        return SuccessExtra(data={"records": records}, total=total, current=cur, size=siz)

    # ── 全量分支（原有逻辑，保持不变） ──
    tier_codes = await resolve_user_tier_codes(role_codes, is_mgr)
    qs = AgentConnector.all()
    if k:
        qs = qs.filter(kind=k)
    if not include_disabled:
        qs = qs.filter(is_enabled=1)
    rows = await qs.order_by("id")
    visible = []
    for c in rows:
        # 管理者可见全部行（上架管理页「管理员看全部」）；普通用户只保留「上架或本人」
        if not is_mgr and not _listed_or_own(c, uid):
            continue
        # 未上架行只对管理者/作者本人可见
        if not c.is_enabled and not (is_mgr or _can_manage(c, uid, is_mgr)):
            continue
        # 档位过滤：不在可见白名单的用户看不到（作者恒见自己实体，在 tier_allows 内）
        if not await tier_allows(c.min_tier_code, c.user_id, uid, tier_codes):
            continue
        visible.append(c)
    records = await _connectors_to_records(visible, uid)
    return Success(data=records)


@router.get("/manage-authors", summary="上架管理：连接器/数据集作者清单（仅管理员）")
async def manage_authors(kind: Optional[str] = None):
    """作者筛选下拉源：按作者分组计数 [{userId, author, count}]（userId=None 为「官方」桶）。

    kind 可选：connector / dataset，区分连接器与数据集（共表）；不传=合计。
    """
    from app.api.v1.ai.agent_skill import _author_facets

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可见")
    k = (kind or "").strip() or None
    if k is not None and k not in _VALID_KINDS:
        return Fail(msg="类别无效：connector 或 dataset")
    qs = AgentConnector.all()
    if k:
        qs = qs.filter(kind=k)
    return Success(data=await _author_facets(qs))


@router.post("", summary="新建连接器（默认未上架，仅创建者可见）")
async def create_connector(req: ConnectorCreateReq):
    """所有人同一套流程：填的凭据是本人的，存个人凭据表。
    credential_mode 自动推导：填了凭据=personal（各自填写），没填=none。"""
    from app.api.v1.ai.agent_skill import _norm_example_questions

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录，无法创建连接器")
    name = (req.name or "").strip()
    if not name or len(name) > 64:
        return Fail(msg="连接器名称需为 1~64 字")
    kind = (req.kind or "connector").strip() or "connector"
    if kind not in _VALID_KINDS:
        return Fail(msg="类别无效：connector 或 dataset")
    err = _validate_endpoint((req.transport or "").strip(), (req.url or "").strip())
    if err:
        return Fail(msg=err)
    key = (req.api_key or "").strip() or None
    c = await AgentConnector.create(
        connector_key=f"conn_{secrets.token_hex(4)}",
        name=name,
        description=(req.description or "").strip()[:1000] or None,
        icon=(req.icon or "").strip() or None,
        kind=kind,
        transport=req.transport.strip(),
        url=req.url.strip(),
        credential_mode="personal" if key else "none",
        api_key=None,  # 共享凭据只由「带上凭据上架」写入，创建时不落
        user_id=uid,
        is_enabled=0,
        example_questions=_norm_example_questions(req.example_questions) or None,
    )
    if key:
        await AgentConnectorCredential.update_or_create(
            defaults={"api_key": key}, user_id=uid, connector_key=c.connector_key
        )
    _evict_user_agents(uid)
    logger.info(f"[connector] 新建连接器 id={c.id} key={c.connector_key} transport={c.transport} host={urlparse(c.url).netloc}")
    return Success(data=_connector_to_dict(c, True, True, author=None, has_my_key=bool(key)))


async def _get_connector_by_id(connector_id: int) -> Optional[AgentConnector]:
    return await AgentConnector.get_or_none(id=connector_id)


@router.patch("/{connector_id}", summary="更新连接器定义（创建者/管理员；api_key=共享凭据）")
async def update_connector(connector_id: int, req: ConnectorUpdateReq):
    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    c = await _get_connector_by_id(connector_id)
    if c is None:
        return Fail(msg="连接器不存在")
    if not _can_manage(c, uid, is_mgr):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可编辑该连接器")

    # 可见档位白名单：仅管理员可设置；[]/'all' 归一为 NULL（全员可见）；非法档位 code → 4000；不传不改
    if req.min_tier_code is not None:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可设置可见档位")
        norm, terr = await norm_tier_codes(req.min_tier_code)
        if terr:
            return Fail(msg=terr)
        c.min_tier_code = norm
    if req.is_enabled is not None and bool(c.is_enabled) != req.is_enabled:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可上下架连接器")
        if req.is_enabled and req.with_credential == "mine":
            # 带上我的凭据上架：把操作者本人的凭据复制为共享凭据（全员共用）
            mine = await AgentConnectorCredential.get_or_none(user_id=uid, connector_key=c.connector_key)
            if mine is None or not mine.api_key:
                return Fail(msg="你还没有为该连接器填写凭据：请先点「编辑」填好自己的凭据，再选择「带上凭据上架」")
            c.api_key = mine.api_key
            c.credential_mode = "shared"
        elif req.is_enabled and req.with_credential == "keep":
            # 保留已有共享凭据（编辑里配置过的）；没有则按无凭据处理
            if not c.api_key:
                c.credential_mode = await _derive_mode_without_shared(c)
        elif req.is_enabled:
            # 不带凭据上架：清掉共享凭据，用户各自填写（按创建者有无凭据派生 personal/none）
            c.api_key = None
            c.credential_mode = await _derive_mode_without_shared(c)
        else:
            # 下架：清掉共享凭据（共享凭据只存在于上架期间）
            c.api_key = None
            c.credential_mode = await _derive_mode_without_shared(c)
        c.is_enabled = 1 if req.is_enabled else 0
    if req.name is not None:
        name = req.name.strip()
        if not name or len(name) > 64:
            return Fail(msg="连接器名称需为 1~64 字")
        c.name = name
    if req.kind is not None:
        kind = req.kind.strip()
        if kind not in _VALID_KINDS:
            return Fail(msg="类别无效：connector 或 dataset")
        c.kind = kind
    if req.description is not None:
        c.description = req.description.strip()[:1000] or None
    if req.icon is not None:
        c.icon = req.icon.strip() or None
    transport = (req.transport or "").strip() or c.transport
    url = (req.url or "").strip() or c.url
    if req.transport is not None or req.url is not None:
        err = _validate_endpoint(transport, url)
        if err:
            return Fail(msg=err)
        c.transport = transport
        c.url = url
    if req.shared_api_key is not None:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可调整共享凭据")
        key = req.shared_api_key.strip()
        if key:
            c.api_key = key
            c.credential_mode = "shared"
        else:
            c.api_key = None
            c.credential_mode = await _derive_mode_without_shared(c)
    if req.api_key is not None:
        # 个人凭据一律落操作者自己的凭据表（共享凭据由 shared_api_key / 上架操作管理）
        key = req.api_key.strip()
        if key:
            await AgentConnectorCredential.update_or_create(
                defaults={"api_key": key}, user_id=uid, connector_key=c.connector_key
            )
        else:
            await AgentConnectorCredential.filter(user_id=uid, connector_key=c.connector_key).delete()
        # 非 shared 状态下刷新派生 mode（创建者补了凭据 → none 变 personal）
        if c.credential_mode != "shared":
            c.credential_mode = await _derive_mode_without_shared(c)
    if req.example_questions is not None:
        from app.api.v1.ai.agent_skill import _norm_example_questions

        c.example_questions = _norm_example_questions(req.example_questions) or None

    await c.save()
    _evict_user_agents(uid)
    from app.api.v1.ai.agent_skill import _author_label_map

    author_map = await _author_label_map([c.user_id] if c.user_id is not None else [])
    cred_map = await _my_credential_map(uid)
    return Success(data=_connector_to_dict(c, author=author_map.get(c.user_id), has_my_key=c.connector_key in cred_map))


@router.put("/{connector_id}/credential", summary="设置我的个人凭据（一人一份；空串=清除）")
async def set_my_credential(connector_id: int, req: ConnectorCredentialReq):
    """仅 credential_mode=personal 的连接器：每个用户在这里填自己的 api_key（一人一份、互不可见）。
    未配置个人凭据的 personal 连接器不会被 agent 加载，卡片显示「待配置凭据」。"""
    uid, role_codes, is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录，无法设置凭据")
    c = await _get_connector_by_id(connector_id)
    if c is None:
        return Fail(msg="连接器不存在")
    if not _listed_or_own(c, uid):
        return Fail(code="4032", msg="forbidden: 无权操作该连接器")
    _tier_codes = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    if not await tier_allows(c.min_tier_code, c.user_id, uid, _tier_codes):
        return Fail(code="4032", msg="forbidden: 无权操作该连接器")
    if c.credential_mode == "shared" and c.user_id != uid:
        return Fail(msg="该连接器已带共享凭据，添加后直接可用，无需再填")
    key = (req.api_key or "").strip()
    if key:
        await AgentConnectorCredential.update_or_create(
            defaults={"api_key": key}, user_id=uid, connector_key=c.connector_key
        )
    else:
        await AgentConnectorCredential.filter(user_id=uid, connector_key=c.connector_key).delete()
    _evict_user_agents(uid)
    logger.info(f"[connector] 更新个人凭据 key={c.connector_key} uid={uid} action={'set' if key else 'clear'}")
    from app.api.v1.ai.agent_skill import _author_label_map

    author_map = await _author_label_map([c.user_id] if c.user_id is not None else [])
    return Success(data=_connector_to_dict(c, author=author_map.get(c.user_id), has_my_key=bool(key)))


@router.delete("/{connector_id}", summary="删除连接器（创建者/管理员）")
async def delete_connector(connector_id: int):
    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    c = await _get_connector_by_id(connector_id)
    if c is None:
        return Fail(msg="连接器不存在")
    if not _can_manage(c, uid, is_mgr):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可删除该连接器")
    await AgentConnectorUserPref.filter(connector_key=c.connector_key).delete()
    await AgentConnectorCredential.filter(connector_key=c.connector_key).delete()
    await c.delete()
    _evict_user_agents(uid)
    logger.info(f"[connector] 删除连接器 id={connector_id} key={c.connector_key}")
    return Success(data={"deleted": connector_id})


@router.put("/prefs", summary="批量设置连接器个人偏好（添加/移除、启用/禁用，只影响当前用户）")
async def batch_connector_prefs(req: ConnectorPrefsReq):
    """与技能偏好同口径：is_added=是否加进「我的连接器」，is_enabled=个人启停（正交）。
    全局 is_enabled（商店上架/下架）不动。agent 工具集随签名自动重建。"""
    uid, role_codes, is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录，无法设置连接器偏好")
    if req.is_enabled is None and req.is_added is None:
        return Fail(msg="is_enabled 与 is_added 至少传一个")
    keys = list(dict.fromkeys(req.connector_keys))
    if not keys:
        return Fail(msg="connector_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多设置 200 个连接器")
    tier_codes = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    # 只处理存在且对当前用户「上架或本人 且 在可见白名单内」的连接器；其余静默过滤（堵 API 直调绕过）
    rows = await AgentConnector.filter(connector_key__in=keys)
    valid_keys = [
        c.connector_key
        for c in rows
        if _listed_or_own(c, uid) and await tier_allows(c.min_tier_code, c.user_id, uid, tier_codes)
    ]
    defaults: dict = {}
    if req.is_enabled is not None:
        defaults["is_enabled"] = 1 if req.is_enabled else 0
    if req.is_added is not None:
        defaults["is_added"] = 1 if req.is_added else 0
    for k in valid_keys:
        await AgentConnectorUserPref.update_or_create(
            defaults=defaults, user_id=uid, connector_key=k
        )
    if valid_keys:
        _evict_user_agents(uid)
    return Success(data={"updated": valid_keys})


# ── 上架管理批量操作（管理员） ───────────────────────────────────────────────
# 路径纪律同技能侧：单段字面路由用 PUT/POST，勿改 DELETE/PATCH 单段变体，
# 会被先注册的 /{connector_id} 参数路由抢先匹配。


@router.put("/manage", summary="批量管理连接器（上架/下架，仅管理员）")
async def batch_manage_connectors(req: ConnectorManageBatchReq):
    """上架管理页的批量操作（单条语义同 PATCH /{connector_id}，权限同：仅管理员）。"""
    uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅管理员可批量管理连接器")
    if req.is_enabled is None:
        return Fail(msg="is_enabled 必传")
    keys = list(dict.fromkeys(req.connector_keys))
    if not keys:
        return Fail(msg="connector_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多管理 200 个连接器")
    rows = await AgentConnector.filter(connector_key__in=keys)
    updated: list[str] = []
    skipped: list[str] = []
    by_key = {c.connector_key: c for c in rows}
    for k in keys:
        c = by_key.get(k)
        if c is None:
            skipped.append(k)
            continue
        if bool(c.is_enabled) != bool(req.is_enabled):
            # 批量上/下架一律不带凭据（带凭据上架需逐个确认）：清共享凭据、派生模式
            c.api_key = None
            c.credential_mode = await _derive_mode_without_shared(c)
        c.is_enabled = 1 if req.is_enabled else 0
        await c.save()
        updated.append(k)
    if updated:
        _evict_user_agents(uid)
    return Success(data={"updated": updated, "skipped": skipped})


@router.post("/manage-delete", summary="批量删除连接器（管理员可删全部；作者可删自己的）")
async def batch_delete_connectors(req: ConnectorKeysReq):
    """上架管理页批量删除：主记录 + 所有用户偏好行 + 个人凭据行一并删除。"""
    from tortoise.transactions import in_transaction

    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    keys = list(dict.fromkeys(req.connector_keys))
    if not keys:
        return Fail(msg="connector_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多删除 200 个连接器")
    rows = await AgentConnector.filter(connector_key__in=keys)
    by_key = {c.connector_key: c for c in rows}
    updated: list[str] = []
    skipped: list[str] = []
    for k in keys:
        c = by_key.get(k)
        if c is None or not _can_manage(c, uid, is_mgr):
            skipped.append(k)
            continue
        try:
            async with in_transaction("conn_standard"):
                await AgentConnectorUserPref.filter(connector_key=k).delete()
                await AgentConnectorCredential.filter(connector_key=k).delete()
                await c.delete()
            updated.append(k)
        except Exception:  # noqa: BLE001
            logger.exception(f"批量删除连接器失败 {k}")
            skipped.append(k)
    if updated:
        _evict_user_agents(uid)
    return Success(data={"updated": updated, "skipped": skipped})


# ── 连接测试（保存前/保存后试连 MCP server，列出工具） ─────────────────────


@router.post("/test", summary="试连 MCP server，返回其提供的工具列表")
async def test_connector(req: ConnectorTestReq):
    """凭据解析顺序：显式传入 > 我的个人凭据 > 连接器共享凭据；keySource 回显本次用了哪个。"""
    uid, _role_codes, _is_super = await get_current_role_codes()
    _tier_codes = await resolve_user_tier_codes(_role_codes, _is_manager(_is_super, _role_codes))

    key_source = "none"
    if req.id is not None:
        c = await _get_connector_by_id(req.id)
        if c is None:
            return Fail(msg="连接器不存在")
        if not _listed_or_own(c, uid):
            return Fail(code="4032", msg="forbidden: 无权测试该连接器")
        if not await tier_allows(c.min_tier_code, c.user_id, uid, _tier_codes):
            return Fail(code="4032", msg="forbidden: 无权测试该连接器")
        transport, url = c.transport, c.url
        api_key: Optional[str] = None
        if uid is not None:
            mine = await AgentConnectorCredential.get_or_none(user_id=uid, connector_key=c.connector_key)
            if mine is not None and mine.api_key:
                api_key = mine.api_key
                key_source = "mine"
        if api_key is None and c.credential_mode == "shared" and c.api_key:
            api_key = c.api_key
            key_source = "shared"
        if api_key is None and c.credential_mode == "personal":
            return Fail(msg="该连接器需要各自填写凭据：请先在「配置凭据」里填写你的 api_key 再试连")
    else:
        transport = (req.transport or "").strip()
        url = (req.url or "").strip()
        api_key = (req.api_key or "").strip() or None
        key_source = "given" if api_key else "none"
        err = _validate_endpoint(transport, url)
        if err:
            return Fail(msg=err)

    from langchain_mcp_adapters.sessions import create_session

    conn = _build_connection(transport, url, api_key)

    async def _probe() -> list:
        async with create_session(conn) as session:
            await session.initialize()
            result = await session.list_tools()
            return result.tools

    try:
        tools = await asyncio.wait_for(_probe(), timeout=_TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(f"[connector] 试连超时 host={urlparse(url).netloc}")
        other = "SSE" if transport == "streamable_http" else "Streamable HTTP"
        return Fail(msg=f"连接超时（{_TEST_TIMEOUT_S:.0f}s）：服务器无响应，请检查 URL，或换选 {other} 再试（SSE 与 Streamable HTTP 互不兼容，协议选错会一直等待）")
    except Exception as e:
        logger.warning(f"[connector] 试连失败 host={urlparse(url).netloc} err={type(e).__name__}")
        return Fail(msg=_classify_conn_error(e, api_key))

    data_tools = [
        {"name": getattr(t, "name", "") or "", "description": (getattr(t, "description", "") or "")[:200]}
        for t in tools
    ]
    return Success(data={"tools": data_tools, "toolCount": len(data_tools), "keySource": key_source})
