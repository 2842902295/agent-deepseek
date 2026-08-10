"""
标准化对象分类体系

模块加载时自动解析 standard_object_taxonomy.txt，对外暴露结构化数据。

用法：
    from app.langchain.agents.mind_map import TAXONOMY, get_element_names, get_object_types
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .parser import MindMapNode, parse

_TXT_PATH = Path(__file__).parent / "standard_object_taxonomy.txt"

# 完整节点树
MIND_MAP_ROOT: MindMapNode = parse(_TXT_PATH)

# 三层嵌套字典：{对象类型: {要素组: [要素名称]}}
# 例：TAXONOMY["产品类对象"]["规范类要素"] == ["标识", "外在特性", "感官", "性能", "功能", "物质含量", "其他"]
TAXONOMY: dict[str, dict[str, list[str]]] = {}

for _obj_node in MIND_MAP_ROOT.children:
    TAXONOMY[_obj_node.name] = {}
    for _group_node in _obj_node.children:
        TAXONOMY[_obj_node.name][_group_node.name] = [
            e.name for e in _group_node.children
        ]


def get_object_types() -> list[str]:
    """返回所有对象类型名称，如 ['产品类对象', '服务类对象', '过程类对象']。"""
    return list(TAXONOMY.keys())


def get_element_names(object_type: str) -> list[str]:
    """返回指定对象类型下所有要素名称（扁平、去重、保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for elements in TAXONOMY.get(object_type, {}).values():
        for e in elements:
            if e not in seen:
                seen.add(e)
                result.append(e)
    return result


def get_element_groups(object_type: str) -> dict[str, list[str]]:
    """返回指定对象类型的 {要素组: [要素]} 字典。"""
    return TAXONOMY.get(object_type, {})


def find_node(name: str) -> Optional[MindMapNode]:
    """在整棵树中按名称 BFS 查找节点。"""
    return MIND_MAP_ROOT.find(name)


def get_annotation(object_type: str, element: str) -> list[str]:
    """返回指定对象类型下某要素节点的注释列表。"""
    obj_node = MIND_MAP_ROOT.find(object_type)
    if obj_node is None:
        return []
    elem_node = obj_node.find(element)
    if elem_node is None:
        return []
    return elem_node.annotations


def build_taxonomy_tree_desc() -> str:
    """
    生成三级分类树形描述字符串，供 LLM 提示词中嵌入分类约束。

    输出示例：
        object_type → norm_class → indicator_category，从以下树中选一条路径（三字段必须属于同一分支）：

        【产品类对象】
          规范类要素 → indicator_category: 标识 | 外在特性 | 感官 | 性能 | 功能 | 物质含量 | 其他
          ...
    """
    lines = [
        "object_type → norm_class → indicator_category，从以下树中选一条路径（三字段必须属于同一分支）：",
    ]
    for obj_type, groups in TAXONOMY.items():
        lines.append(f"\n【{obj_type}】")
        for group_name, elements in groups.items():
            lines.append(f"  {group_name} → indicator_category: {' | '.join(elements)}")
    return "\n".join(lines)


__all__ = [
    "MIND_MAP_ROOT",
    "TAXONOMY",
    "get_object_types",
    "get_element_names",
    "get_element_groups",
    "find_node",
    "get_annotation",
    "build_taxonomy_tree_desc",
    "MindMapNode",
    "parse",
]
