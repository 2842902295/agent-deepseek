"""
思维导图解析器

解析空格缩进格式的思维导图文本，构建节点树。
缩进规则：每级缩进 1 个空格，超过 structural_depth 的行视为注释，收集到父节点 annotations。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 结构层级深度上限（0=根, 1=对象类型, 2=要素组, 3=要素），超过此深度的行为注释
STRUCTURAL_DEPTH = 3


@dataclass
class MindMapNode:
    name: str
    depth: int
    parent: Optional["MindMapNode"] = field(default=None, repr=False, compare=False)
    children: list["MindMapNode"] = field(default_factory=list, repr=False)
    annotations: list[str] = field(default_factory=list, repr=False)

    def path(self) -> list[str]:
        """从根到本节点的完整路径。"""
        nodes: list[str] = []
        cur: Optional[MindMapNode] = self
        while cur is not None:
            nodes.append(cur.name)
            cur = cur.parent
        return list(reversed(nodes))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "children": [c.to_dict() for c in self.children],
            "annotations": self.annotations,
        }

    def find(self, name: str) -> Optional["MindMapNode"]:
        """BFS 查找指定名称的子节点。"""
        queue = list(self.children)
        while queue:
            node = queue.pop(0)
            if node.name == name:
                return node
            queue.extend(node.children)
        return None


def parse(file_path: str | Path, structural_depth: int = STRUCTURAL_DEPTH) -> MindMapNode:
    """
    解析空格缩进思维导图文本，返回根节点。

    depth > structural_depth 的行视为注释，收集到最近结构节点的 annotations，不进入 children。
    """
    text = Path(file_path).read_text(encoding="utf-8")
    lines = text.splitlines()

    root: Optional[MindMapNode] = None
    struct_stack: list[MindMapNode] = []

    for raw in lines:
        if not raw.strip():
            continue

        depth = len(raw) - len(raw.lstrip(" "))
        name = raw.strip()

        # 超过结构深度的行视为注释
        if depth > structural_depth:
            if struct_stack:
                struct_stack[-1].annotations.append(name)
            continue

        # 结构节点
        node = MindMapNode(name=name, depth=depth)

        while struct_stack and struct_stack[-1].depth >= depth:
            struct_stack.pop()

        if struct_stack:
            parent = struct_stack[-1]
            node.parent = parent
            parent.children.append(node)
        else:
            root = node

        struct_stack.append(node)

    if root is None:
        raise ValueError(f"空文件或无法解析：{file_path}")
    return root
