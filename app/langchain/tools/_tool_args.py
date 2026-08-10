"""
工具入参兼容层：还原"被字符串化"的数组 / 对象参数

部分模型（如 qwen 系）偶尔把数组 / 对象类工具入参输出成 JSON 字符串：
grant_menu_ids="[1, 2]"、"1,2" 而不是 [1, 2]。pydantic 校验直接拒绝
（Input should be a valid list），agent 反复重试同一错误形态，最终对用户
谎报"平台不支持"。项目里 workflow_tools 用"数组一律传 JSON 字符串"、
edit_tools.skill_save.files 被迫用 Any，都是同一问题的绕法。

统一正解：包装工具的 args_schema，在校验前把字符串化的数组 / 对象还原成
真实结构：
- "[1, 2]" / '{"a": 1}'  → json.loads 解析
- "1,2" / "R_USER、R_SUPER" → 按逗号 / 、 / 分号 / 空白拆成 list
- "15"（单值但目标为 list）→ [15]

用法：
- 工具工厂已有统一包装器（如 admin_tools 的 _guarded）：在包装器里用
  lenient_args_schema(t.args_schema) 替换 schema
- 否则在工厂返回前对每个工具套 with_lenient_args(tool)
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


def _container_kind(anno) -> Optional[str]:
    """参数注解是否为 list / dict 容器（含 Optional / Union 包装）。返回 "list" / "dict" / None。"""
    origin = typing.get_origin(anno)
    if origin in (typing.Union, getattr(_pytypes, "UnionType", None)):
        for a in typing.get_args(anno):
            k = _container_kind(a)
            if k:
                return k
        return None
    if origin is list or anno is list:
        return "list"
    if origin is dict or anno is dict:
        return "dict"
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


def lenient_args_schema(schema):
    """包装工具的 args_schema：校验前还原字符串化的数组 / 对象参数。

    无参工具（schema=None）与无容器参数的 schema 原样返回。
    JSON schema（给模型看的）不变，只放宽运行期校验。
    """
    if schema is None:
        return None
    kinds = {name: _container_kind(f.annotation) for name, f in schema.model_fields.items()}
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
                if kind and isinstance(v, str):
                    restored = _restore_str(v, kind)
                    if restored is not v:
                        logger.info(f"[tool_args] 还原字符串化入参 {k}: {v[:80]!r} -> {type(restored).__name__}")
                    out[k] = restored
            return out

    _Lenient.__name__ = schema.__name__
    return _Lenient


def with_lenient_args(t):
    """给单个工具套上兼容 args_schema；无容器参数的工具原样返回。"""
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
