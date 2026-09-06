"""
工具入参兼容层：还原"被字符串化"的数组 / 对象 / 布尔参数

部分模型（如 qwen 系）偶尔把数组 / 对象类工具入参输出成 JSON 字符串：
grant_menu_ids="[1, 2]"、"1,2" 而不是 [1, 2]。pydantic 校验直接拒绝
（Input should be a valid list），agent 反复重试同一错误形态，最终对用户
谎报"平台不支持"。项目里 workflow_tools 用"数组一律传 JSON 字符串"、
edit_tools.skill_save.files 被迫用 Any，都是同一问题的绕法。

布尔参数同样会被字符串化：prompt_extend="false" / "否" / "不需要改写"。
pydantic lax 模式只认 "true"/"false"/"0"/"1" 等英文词，中文布尔串直接报
bool_parsing 错误、整个工具调用失败；而裸 `bool("false")` 又是 True，
语义正好反掉。

统一正解分两层，缺一不可：

1. **MCP 门面放宽**（tool_json_schema）：agent 的工具调用全部经 MCP 桥，
   MCP SDK lowlevel server 在 call_tool 前会 `jsonschema.validate(args,
   inputSchema)`——字符串化入参在这里就被拦死（"Input validation error:
   ... not valid under any of the given schemas"），根本到不了运行期。
   因此暴露给 MCP 的 inputSchema 必须给 bool / array / object / 数字
   （integer/number）参数加上 string 备选。数字同样高频被字符串化
   （record_id="4"），不放行则一切带 id 的写操作全被门面拦死。
2. **运行期还原**（lenient_args_schema / with_lenient_args）：字符串进了
   工具后，在 pydantic 校验前还原成真实结构：
   - "[1, 2]" / '{"a": 1}'  → json.loads 解析
   - "1,2" / "R_USER、R_SUPER" → 按逗号 / 、 / 分号 / 空白拆成 list
   - "15"（单值但目标为 list）→ [15]
   - "false" / "否" / "不需要" / "关闭" → False；"true" / "是" / "开启" → True
   - 数字串（"4" / "3.5"）无需显式还原：pydantic lax 模式对 int/float
     字段自带字符串强转，门面放行即可

用法：
- 工具工厂已有统一包装器（如 admin_tools 的 _guarded）：在包装器里用
  lenient_args_schema(t.args_schema) 替换 schema
- 否则在工厂返回前对每个工具套 with_lenient_args(tool)
- MCP 桥注册工具时，inputSchema 一律用 tool_json_schema(t) 生成
"""

from __future__ import annotations

import json
import logging
import re
import types as _pytypes
import typing
from typing import Any, Optional

import pydantic

logger = logging.getLogger(__name__)

# 兼容"逗号 / 中文逗号 / 顿号 / 分号 / 空白"分隔的列表字符串
_SPLIT_RE = re.compile(r"[,，、;\s]+")


def _param_kind(anno) -> Optional[str]:
    """参数注解是否为需要还原的 list / dict / bool 类型（含 Optional / Union 包装）。

    返回 "list" / "dict" / "bool" / None；Union 内混有容器时容器优先。
    """
    origin = typing.get_origin(anno)
    if origin in (typing.Union, getattr(_pytypes, "UnionType", None)):
        kinds = {_param_kind(a) for a in typing.get_args(anno)}
        kinds.discard(None)
        for k in ("list", "dict", "bool"):
            if k in kinds:
                return k
        return None
    if origin is list or anno is list:
        return "list"
    if origin is dict or anno is dict:
        return "dict"
    if anno is bool:
        return "bool"
    return None


def _restore_str(s: str, kind: str) -> Any:
    """把字符串化的入参还原为真实结构；还原不动就原样返回，交给校验报清晰错误。"""
    t = s.strip()
    if not t:
        return None
    if t[0] in "[{":
        try:
            parsed = json.loads(t)
        except Exception:
            return s
        if kind == "list" and not isinstance(parsed, list):
            return [parsed]  # 单值被包成 JSON 标量：'[15]' → 15 → [15]
        return parsed
    if kind == "list":
        parts = [p.strip().strip("'\"") for p in _SPLIT_RE.split(t) if p.strip()]
        if parts:
            return parts
    return s


# ── 布尔还原 ─────────────────────────────────────────────────────────────────
# 英文词大部分场景 pydantic lax 模式自己能吃下，这里统一先还原成真布尔，
# 重点兜住中文表达："否" / "不需要" / "关闭" / "开启" / "带声音"……
_TRUE_WORDS = {
    "true",
    "1",
    "yes",
    "y",
    "t",
    "on",
    "是",
    "开",
    "开启",
    "要",
    "需要",
    "真",
    "启用",
    "打开",
    "加",
    "带",
    "固定",
}
_FALSE_WORDS = {
    "false",
    "0",
    "no",
    "n",
    "f",
    "off",
    "否",
    "关",
    "关闭",
    "不要",
    "不需要",
    "假",
    "禁用",
    "取消",
}
# 中文短语前缀兜底（"不需要智能改写" / "开启声音"）；否定前缀优先判定
_FALSE_PREFIXES = ("不", "否", "无", "关", "别", "禁", "取消")
_TRUE_PREFIXES = ("开", "要", "需", "启", "打", "加", "带", "固定")


def _restore_bool_str(s: str):
    """把字符串化的布尔入参还原为真布尔；识别不了返回 None，留给校验报清晰错误。"""
    t = s.strip().lower()
    if not t:
        return None
    if t in _TRUE_WORDS:
        return True
    if t in _FALSE_WORDS:
        return False
    if t.startswith(_FALSE_PREFIXES):
        return False
    if t.startswith(_TRUE_PREFIXES):
        return True
    return None


def lenient_args_schema(schema):
    """包装工具的 args_schema：校验前还原字符串化的数组 / 对象 / 布尔参数。

    无参工具（schema=None）与无相关类型参数的 schema 原样返回。
    JSON schema（给模型看的）不变，只放宽运行期校验。
    """
    if schema is None:
        return None
    kinds = {name: _param_kind(f.annotation) for name, f in schema.model_fields.items()}
    if not any(kinds.values()):
        return schema

    class _Lenient(schema):
        @pydantic.model_validator(mode="before")
        @classmethod
        def _restore_stringified(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            out = dict(data)
            for k, v in out.items():
                kind = kinds.get(k)
                if not kind or not isinstance(v, str):
                    continue
                if kind == "bool":
                    restored = _restore_bool_str(v)
                    if restored is None:
                        continue
                else:
                    restored = _restore_str(v, kind)
                    if restored is v:
                        continue
                logger.info(f"[tool_args] 还原字符串化入参 {k}: {v[:80]!r} -> {restored!r}")
                out[k] = restored
            return out

    _Lenient.__name__ = schema.__name__
    return _Lenient


def with_lenient_args(t):
    """给单个工具套上兼容 args_schema；无相关类型参数的工具原样返回。"""
    from langchain_core.tools import StructuredTool

    old_schema = getattr(t, "args_schema", None)
    new_schema = lenient_args_schema(old_schema)
    if new_schema is old_schema:
        return t
    return StructuredTool.from_function(
        func=getattr(t, "func", None),
        coroutine=getattr(t, "coroutine", None),
        name=t.name,
        description=t.description,
        args_schema=new_schema,
    )


# ── MCP 门面：inputSchema 放宽 ───────────────────────────────────────────────

# bool / array / object / 数字(integer/number) 参数允许以字符串形式传入。
# 数字纳入放宽的原因：模型经常把 record_id / xxx_id 这类整数发成字符串 "4"，
# 而 Optional[int] 的 schema 是 anyOf[integer,null]，jsonschema 三个分支都不匹配
# string → 门面当场拦死（'4' is not valid under any of the given schemas），根本
# 进不到函数体。放宽后字符串数字先过门面，运行期由 pydantic lax 模式还原成 int/float。
_LOOSE_KINDS = {"boolean", "array", "object", "integer", "number"}


def _loosen_str_alt(prop: Any) -> Any:
    """给 bool / array / object / 数字 参数 schema 追加 string 备选，放过字符串化实参。"""
    if not isinstance(prop, dict):
        return prop
    if prop.get("type") in _LOOSE_KINDS:
        out = {k: v for k, v in prop.items() if k != "type"}
        out["anyOf"] = [{"type": prop["type"]}, {"type": "string"}]
        return out
    if isinstance(prop.get("anyOf"), list):
        kinds = {m.get("type") for m in prop["anyOf"] if isinstance(m, dict)}
        if kinds & _LOOSE_KINDS and "string" not in kinds:
            return {**prop, "anyOf": [*prop["anyOf"], {"type": "string"}]}
    return prop


def tool_json_schema(t) -> dict:
    """生成工具的 MCP inputSchema：args_schema 的 JSON schema + 字符串备选放宽。

    MCP 桥注册工具一律用它，而不是裸的 args_schema.model_json_schema()——
    MCP SDK 会在 call_tool 前按 inputSchema 做 jsonschema 校验，字符串化入参
    （"false" / "[1,2]" / '{"a":1}' / "4"）不加 string 备选会被直接拦死，到不了
    运行期还原层（见模块 docstring）。
    """
    try:
        schema = t.args_schema.model_json_schema() if getattr(t, "args_schema", None) else {"type": "object", "properties": {}}
    except Exception:  # noqa: BLE001
        return {"type": "object", "properties": {}}
    props = schema.get("properties")
    if isinstance(props, dict):
        schema = {**schema, "properties": {k: _loosen_str_alt(v) for k, v in props.items()}}
    return schema


def split_str_list(v) -> Optional[list]:
    """把 'a,b' / 'a、b' 字符串拆成 list；list/tuple 原样转 list；None/空 → None。

    用于函数体内二次兜底：args_schema 只管顶层参数，dict 类参数（如
    admin_save_record 的 changes）内部的列表值仍需体内归一。
    """
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [p.strip().strip("'\"") for p in _SPLIT_RE.split(v) if p.strip()] or None
    return [v]
