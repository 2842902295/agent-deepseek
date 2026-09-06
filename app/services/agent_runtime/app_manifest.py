"""
应用制作「互动接口」manifest（apps/{wk}/mcp.json）读取 / 校验 / 指纹 / 占位符渲染

agent 在开发应用时可写一份声明式 manifest，列出这个应用对外开放的 MCP 工具
（每个工具 = 一组对 `agent_app_row` 行数据的 read/put/append/delete 效果 + 占位符模板）。
平台据此挂真实 MCP 服务（app_board_bridge.py），用户在对话中即可直接与应用互动
（五子棋落子、多 NPC 社区发言……）。本模块是桥与 qa.py 共用的真相源：

- load_manifest_sync / aload_manifest：mtime+size 缓存读盘，返回 (manifest, err, fingerprint)。
  fingerprint = md5(f"{wk}|{raw_bytes}")[:10]，编进 agent cache_key → manifest 改写即触发
  agent 重建（开发迭代闭环）。
- validate_manifest：把 manifest 校验成「要么完全合法、要么带 tools[i].effects[j] 定位的
  明确错误」——错误消息面向 agent 自修，绝不放行半残工具面。
- render_template：执行期把 ${args.x} / ${row.f} / ${ctx.uid|session|ts|tool} 占位符
  替换成实值；整串单占位符保留原类型、内嵌占位符字符串拼接、未知占位符抛 TemplateError。

红线：tbl 一律过 html_app._valid_tbl（与页面行通道同一把尺子），且 **$acl 一律拒绝**
（权限治理面只归 update_app_rows，manifest 不得触碰）。占位符纯字符串替换，绝无表达式运算。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

# ── 单一真相源（延迟导入防循环）──────────────────────────────────────────────
# 路径与表名校验直接复用页面行通道 html_app，杜绝两处规则漂移。
# 不能在模块级 import：app.api.v1.ai 包 __init__ 顶层拉 qa.py，而 qa.py / 桥
# 反过来 import 本模块——模块级导入会在部分初始化状态下炸 ImportError。
# 运行期首次调用时全应用已加载完毕，缓存后开销仅一次 dict 查找。
_consts: Optional[tuple[Path, str, Any]] = None  # (_USERS_ROOT, _ACL_TBL, _valid_tbl)


def _shared_consts() -> tuple[Path, str, Any]:
    global _consts
    if _consts is None:
        from app.api.v1.ai.html_app import _ACL_TBL, _USERS_ROOT, _valid_tbl

        _consts = (_USERS_ROOT, _ACL_TBL, _valid_tbl)
    return _consts

# ── 规格上限 ─────────────────────────────────────────────────────────────────
_MANIFEST_MAX_BYTES = 64 * 1024  # 文件 ≤64KB
_TOOLS_MIN = 1
_TOOLS_MAX = 20
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_DISPLAY_NAME_MAX = 32
_DESC_MAX = 2000
_EFFECTS_MIN = 1
_EFFECTS_MAX = 10
_READ_LIMIT_MAX = 1000
_DELETE_MAX = 1000
_APPEND_MAXLEN_MAX = 2000

# inputSchema 属性 type 白名单
_TYPE_WHITELIST = {"string", "integer", "number", "boolean", "array", "object"}
# JSON Schema 高级关键字：校验期一律拒绝（占位符模板体系只支持扁平可预测的结构）
_SCHEMA_FORBIDDEN = {
    "oneOf", "anyOf", "allOf", "not", "$ref", "definitions", "$defs",
    "patternProperties", "dependencies", "if", "then", "else",
}
# 允许出现的 schema 关键字（其余一律拒绝，防 agent 写出平台不认的结构）
_SCHEMA_ALLOWED = {
    "type", "properties", "items", "required", "enum", "description",
    "default", "minimum", "maximum", "minLength", "maxLength",
    "minItems", "maxItems", "title",
}

_EFFECT_OPS = {"read", "put", "append", "delete"}
_CTX_KEYS = {"uid", "session", "ts", "tool"}

# 占位符：${ns.field}
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]*)\}")
# 整串单占位符（用于类型保留判定）
_SOLO_RE = re.compile(r"^\$\{([^}]*)\}$")


class TemplateError(Exception):
    """占位符渲染错误（缺失字段 / 未知命名空间）。桥执行器捕获后转 {ok:false,...}。"""


# ── 路径与缓存 ───────────────────────────────────────────────────────────────
# path → (mtime, size, manifest, err, fingerprint)；load_manifest_sync 经 to_thread
# 在线程池里跑，故用 threading.Lock 保护（同进程多 worker 也各自一份，无跨进程语义）
_cache: dict[Path, tuple[float, int, Any, Optional[str], str]] = {}
_cache_lock = threading.Lock()


def manifest_path(uid: int, wk: str) -> Path:
    """mcp.json 落盘路径（与 html_app 托管目录同一真相源：users/{uid}/apps/{wk}/）。"""
    users_root = _shared_consts()[0]
    return users_root / str(uid) / "apps" / wk / "mcp.json"


def _fingerprint(wk: str, raw: bytes) -> str:
    """指纹 = md5(f"{wk}|{raw_bytes}")[:10]；wk 拌入杜绝「跨板同内容」串形态。"""
    return hashlib.md5(f"{wk}|".encode("utf-8") + raw).hexdigest()[:10]


def load_manifest_sync(uid: int, wk: str) -> tuple[Optional[dict], Optional[str], str]:
    """同步读 + 校验（供 to_thread 调用）。返回 (manifest|None, err|None, fingerprint)。

    - 文件不存在 → (None, None, "")：合法的「这个应用没有互动接口」，非错误。
    - 超限 / 坏 JSON / 校验失败 → (None, err, fingerprint)：err 面向 agent 自修。
    - 合法 → (manifest, None, fingerprint)。
    mtime+size 命中缓存则直接返回缓存结果（含 err，避免坏文件反复解析）。"""
    p = manifest_path(uid, wk)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None, None, ""
    except OSError as e:
        return None, f"读取 mcp.json 失败：{e!r}", ""

    if st.st_size > _MANIFEST_MAX_BYTES:
        return None, f"mcp.json 超过 {_MANIFEST_MAX_BYTES // 1024}KB 上限（当前 {st.st_size} 字节）", ""

    with _cache_lock:
        hit = _cache.get(p)
    if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2], hit[3], hit[4]

    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, f"读取 mcp.json 失败：{e!r}", ""

    fp = _fingerprint(wk, raw)
    manifest: Optional[dict] = None
    err: Optional[str] = None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        err = f"mcp.json 不是合法 JSON：{e}"
    else:
        verr = validate_manifest(parsed)
        if verr is not None:
            err = verr
        else:
            manifest = parsed

    with _cache_lock:
        _cache[p] = (st.st_mtime, st.st_size, manifest, err, fp)
    return manifest, err, fp


async def aload_manifest(uid: int, wk: str) -> tuple[Optional[dict], Optional[str], str]:
    """异步包装（红线：磁盘 I/O 进线程池，async 端点禁同步阻塞）。"""
    return await asyncio.to_thread(load_manifest_sync, uid, wk)


def invalidate_cache(uid: Optional[int] = None, wk: Optional[str] = None) -> None:
    """清缓存（写 mcp.json 后可主动失效；通常靠 mtime 自动失效，本函数仅兜底）。"""
    with _cache_lock:
        if uid is not None and wk is not None:
            _cache.pop(manifest_path(uid, wk), None)
        else:
            _cache.clear()


# ── 占位符渲染 ───────────────────────────────────────────────────────────────
def _stringify(v: Any) -> str:
    """内嵌占位符的字符串化（JS 惯例：None→空串、bool→true/false）。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _resolve_token(token: str, args: dict, row: Optional[dict], ctx: dict) -> Any:
    """解析单个占位符 token（不含 ${}）为实值。未知命名空间 / 缺字段抛 TemplateError。"""
    token = token.strip()
    if "." not in token:
        raise TemplateError(f"非法占位符 ${{{token}}}（应形如 args.x / row.f / ctx.uid）")
    ns, _, field = token.partition(".")
    field = field.strip()
    if ns == "args":
        if field not in args:
            raise TemplateError(f"缺少入参 args.{field}")
        return args.get(field)
    if ns == "row":
        if row is None:
            return None
        return row.get(field)
    if ns == "ctx":
        if field not in ctx:
            raise TemplateError(f"未知上下文 ctx.{field}（可用：{', '.join(sorted(_CTX_KEYS))}）")
        return ctx.get(field)
    raise TemplateError(f"未知占位符命名空间 ${{{token}}}（仅支持 args / row / ctx）")


def render_template(tpl: Any, args: dict, row: Optional[dict], ctx: dict) -> Any:
    """递归渲染占位符模板。

    - str：整串恰好一个占位符 → 返回原类型实值（含 None）；否则逐个替换为字符串拼接。
    - dict / list：递归渲染值 / 元素（dict 的键按字面保留）。
    - 其余标量（int/float/bool/None）：原样返回。
    未知占位符抛 TemplateError。"""
    if isinstance(tpl, str):
        m = _SOLO_RE.match(tpl)
        if m:
            return _resolve_token(m.group(1), args, row, ctx)

        def _sub(mm: re.Match) -> str:
            return _stringify(_resolve_token(mm.group(1), args, row, ctx))

        return _PLACEHOLDER_RE.sub(_sub, tpl)
    if isinstance(tpl, dict):
        return {k: render_template(v, args, row, ctx) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [render_template(v, args, row, ctx) for v in tpl]
    return tpl


# ── 校验 ─────────────────────────────────────────────────────────────────────
def _scan_placeholders(value: Any) -> list[str]:
    """递归收集模板里出现的所有占位符 token（不含 ${}）。"""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(m.group(1).strip() for m in _PLACEHOLDER_RE.finditer(value))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_scan_placeholders(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_scan_placeholders(v))
    return out


def _check_token_static(token: str, schema_props: set[str], where: str) -> Optional[str]:
    """静态校验一个占位符 token：命名空间合法、args 字段必须在 inputSchema 声明。"""
    if "." not in token:
        return f"{where}：非法占位符 ${{{token}}}（应形如 args.x / row.f / ctx.uid）"
    ns, _, field = token.partition(".")
    field = field.strip()
    if ns == "args":
        if not field:
            return f"{where}：占位符 ${{args.}} 缺字段名"
        if field not in schema_props:
            return f"{where}：模板引用了未在 inputSchema.properties 声明的入参 args.{field}"
        return None
    if ns == "row":
        return None if field else f"{where}：占位符 ${{row.}} 缺字段名"
    if ns == "ctx":
        if field not in _CTX_KEYS:
            return f"{where}：未知上下文 ${{ctx.{field}}}（可用：{', '.join(sorted(_CTX_KEYS))}）"
        return None
    return f"{where}：未知占位符命名空间 ${{{token}}}（仅支持 args / row / ctx）"


def _check_templates(value: Any, schema_props: set[str], where: str) -> Optional[str]:
    """递归校验模板里所有占位符（静态可查的部分）。"""
    for token in _scan_placeholders(value):
        err = _check_token_static(token, schema_props, where)
        if err:
            return err
    return None


def _validate_input_schema(schema: Any, where: str) -> tuple[Optional[str], set[str]]:
    """校验 inputSchema（JSON Schema 子集）。返回 (err, 声明的属性名集合)。"""
    if not isinstance(schema, dict):
        return f"{where}.inputSchema：必须是 JSON 对象", set()
    if schema.get("type") != "object":
        return f'{where}.inputSchema：顶层 type 必须是 "object"', set()
    for forb in _SCHEMA_FORBIDDEN:
        if forb in schema:
            return f"{where}.inputSchema：不支持高级关键字 `{forb}`（只用扁平结构）", set()

    props = schema.get("properties", {})
    if props is None:
        props = {}
    if not isinstance(props, dict):
        return f"{where}.inputSchema.properties：必须是 JSON 对象", set()

    for pname, pdef in props.items():
        pw = f"{where}.inputSchema.properties.{pname}"
        if not isinstance(pdef, dict):
            return f"{pw}：必须是 JSON 对象", set()
        for forb in _SCHEMA_FORBIDDEN:
            if forb in pdef:
                return f"{pw}：不支持高级关键字 `{forb}`", set()
        for kw in pdef:
            if kw not in _SCHEMA_ALLOWED:
                return f"{pw}：含未识别关键字 `{kw}`", set()
        ptype = pdef.get("type")
        if ptype is None:
            return f"{pw}：缺 type", set()
        if isinstance(ptype, list):
            return f"{pw}：type 不支持联合类型数组（写成单一类型）", set()
        if ptype not in _TYPE_WHITELIST:
            return f"{pw}：type `{ptype}` 不在白名单 {sorted(_TYPE_WHITELIST)}", set()

    req = schema.get("required")
    if req is not None:
        if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
            return f"{where}.inputSchema.required：必须是字符串数组", set()
        for r in req:
            if r not in props:
                return f"{where}.inputSchema.required：`{r}` 未在 properties 声明", set()

    return None, set(props.keys())


def _validate_effect(eff: Any, where: str, schema_props: set[str]) -> Optional[str]:
    """校验单个 effect（read/put/append/delete）。"""
    if not isinstance(eff, dict):
        return f"{where}：效果必须是 JSON 对象"
    op = eff.get("op")
    if op not in _EFFECT_OPS:
        return f"{where}：op `{op}` 非法（仅支持 {sorted(_EFFECT_OPS)}）"

    tbl = eff.get("tbl")
    valid_tbl, acl_tbl = _shared_consts()[2], _shared_consts()[1]
    if not valid_tbl(tbl):
        return f"{where}：tbl `{tbl}` 非法（字母/数字/下划线开头，可含 . _ -，≤64 字符）"
    if tbl == acl_tbl:
        return f"{where}：tbl 不得为平台保留表 `{acl_tbl}`（权限治理只归 update_app_rows）"

    if op == "read":
        if "prefix" in eff and "keys" in eff:
            return f"{where}：read 的 prefix 与 keys 不能同时给"
        if "prefix" in eff and not isinstance(eff["prefix"], str):
            return f"{where}：read.prefix 必须是字符串"
        if "keys" in eff:
            keys = eff["keys"]
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                return f"{where}：read.keys 必须是字符串（模板）数组"
            err = _check_templates(keys, schema_props, f"{where}.keys")
            if err:
                return err
        lim = eff.get("limit")
        if lim is not None:
            if not isinstance(lim, int) or isinstance(lim, bool) or lim < 1 or lim > _READ_LIMIT_MAX:
                return f"{where}：read.limit 必须是 1~{_READ_LIMIT_MAX} 的整数"
        return None

    if op == "put":
        key = eff.get("key")
        if not isinstance(key, str) or not key:
            return f"{where}：put.key 必须是非空字符串（模板）"
        err = _check_templates(key, schema_props, f"{where}.key")
        if err:
            return err
        if "data" not in eff or not isinstance(eff["data"], dict):
            return f"{where}：put.data 必须是 JSON 对象（模板）"
        err = _check_templates(eff["data"], schema_props, f"{where}.data")
        if err:
            return err
        if "merge" in eff and not isinstance(eff["merge"], bool):
            return f"{where}：put.merge 必须是布尔"
        return None

    if op == "append":
        key = eff.get("key")
        if not isinstance(key, str) or not key:
            return f"{where}：append.key 必须是非空字符串（模板）"
        err = _check_templates(key, schema_props, f"{where}.key")
        if err:
            return err
        field = eff.get("field")
        if not isinstance(field, str) or not field:
            return f"{where}：append.field 必须是非空字符串（要尾追的数组字段名）"
        if "value" not in eff:
            return f"{where}：append.value 缺失（要追加的元素，模板）"
        err = _check_templates(eff["value"], schema_props, f"{where}.value")
        if err:
            return err
        ml = eff.get("maxLen")
        if ml is not None:
            if not isinstance(ml, int) or isinstance(ml, bool) or ml < 1 or ml > _APPEND_MAXLEN_MAX:
                return f"{where}：append.maxLen 必须是 1~{_APPEND_MAXLEN_MAX} 的整数"
        return None

    # delete
    if "keys" in eff and "prefix" in eff:
        return f"{where}：delete 的 keys 与 prefix 不能同时给"
    if "keys" in eff:
        keys = eff["keys"]
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            return f"{where}：delete.keys 必须是字符串（模板）数组"
        if len(keys) > _DELETE_MAX:
            return f"{where}：delete.keys 单次不得超过 {_DELETE_MAX} 个"
        err = _check_templates(keys, schema_props, f"{where}.keys")
        if err:
            return err
    if "prefix" in eff and not isinstance(eff["prefix"], str):
        return f"{where}：delete.prefix 必须是字符串"
    if "keys" not in eff and "prefix" not in eff:
        return f"{where}：delete 必须给 keys 或 prefix 之一（禁全表删）"
    return None


def _validate_tool(tool: Any, where: str) -> Optional[str]:
    if not isinstance(tool, dict):
        return f"{where}：工具必须是 JSON 对象"

    name = tool.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return f"{where}.name：`{name}` 非法（须匹配 ^[a-z][a-z0-9_]{{1,62}}$）"

    dn = tool.get("displayName")
    if dn is not None and (not isinstance(dn, str) or len(dn) > _DISPLAY_NAME_MAX):
        return f"{where}.displayName：必须是 ≤{_DISPLAY_NAME_MAX} 字符的字符串"

    desc = tool.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return f"{where}.description：必须是非空字符串（LLM 靠它判断何时调用）"
    if len(desc) > _DESC_MAX:
        return f"{where}.description：超过 {_DESC_MAX} 字符上限"

    schema_err, schema_props = _validate_input_schema(tool.get("inputSchema"), where)
    if schema_err:
        return schema_err

    effects = tool.get("effects")
    if not isinstance(effects, list):
        return f"{where}.effects：必须是数组"
    if not (_EFFECTS_MIN <= len(effects) <= _EFFECTS_MAX):
        return f"{where}.effects：数量须在 {_EFFECTS_MIN}~{_EFFECTS_MAX} 之间（当前 {len(effects)}）"
    for j, eff in enumerate(effects):
        err = _validate_effect(eff, f"{where}.effects[{j}]", schema_props)
        if err:
            return err
    return None


def validate_manifest(raw: Any) -> Optional[str]:
    """整份 manifest 校验。返回 None = 合法；返回字符串 = 带定位的错误描述（面向 agent 自修）。"""
    if not isinstance(raw, dict):
        return "mcp.json 顶层必须是 JSON 对象"
    if raw.get("version") != 1:
        return f'mcp.json：version 必须为 1（当前 `{raw.get("version")}`）'
    extra = set(raw.keys()) - {"version", "tools"}
    if extra:
        return f"mcp.json：顶层含未识别字段 {', '.join(sorted(extra))}"

    tools = raw.get("tools")
    if not isinstance(tools, list):
        return "mcp.json：tools 必须是数组"
    if not (_TOOLS_MIN <= len(tools) <= _TOOLS_MAX):
        return f"mcp.json：tools 数量须在 {_TOOLS_MIN}~{_TOOLS_MAX} 之间（当前 {len(tools)}）"

    seen: set[str] = set()
    for i, tool in enumerate(tools):
        err = _validate_tool(tool, f"tools[{i}]")
        if err:
            return err
        nm = tool["name"]
        if nm in seen:
            return f"tools[{i}]：工具名 `{nm}` 重复（manifest 内须唯一）"
        seen.add(nm)
    return None
