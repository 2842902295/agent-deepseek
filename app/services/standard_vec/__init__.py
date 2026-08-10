"""标准向量库 service：嵌入构建 + 增量水位 + 失败重试。"""

from app.services.standard_vec.builder import StandardVecBuilder

__all__ = ["StandardVecBuilder"]
