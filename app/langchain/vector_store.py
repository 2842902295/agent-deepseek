"""
向量库服务

使用 FAISS + Embedding Provider 抽象层构建标准查重向量库
支持 DashScope（阿里云）和 Ollama（本地）两种 Embedding 提供商
"""

import asyncio
import json
import os
import shutil
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import numpy as np
from loguru import logger

from app.langchain.embedding_providers import get_embedding, BaseEmbeddingProvider
from app.models.standard import StandardBaseInfo


class StandardVectorStore:
    """标准向量库服务"""

    def __init__(self):
        self.index = None
        self.metadata = []  # 存储标准的元数据 [{id, cname, use_range}, ...]

        # 获取 Embedding Provider
        self._embedding_provider: BaseEmbeddingProvider = get_embedding()
        self._dimension_detected = self._embedding_provider.dimension_detected

        # 维度：优先从 provider 获取，Ollama 未配置维度时延迟探测
        if self._dimension_detected:
            self.dimension = self._embedding_provider.dimension
        else:
            self.dimension = None  # 延迟到异步方法中探测

        # 文件路径
        self.index_file = "data/standard_faiss_index.npy"
        self.metadata_file = "data/standard_metadata.json"
        self.index_meta_file = "data/standard_index_meta.json"
        self.progress_file = "data/standard_build_progress.json"

        # 构建进度状态
        self.build_status = {
            "is_building": False,
            "total": 0,
            "processed": 0,
            "last_processed_id": None,
            "error": None
        }

    async def _ensure_dimension(self):
        """确保维度已确定（未配置时自动探测）"""
        if not self._dimension_detected:
            self.dimension = await self._embedding_provider.detect_dimension()
            self._dimension_detected = True

    async def build_index(
        self,
        force_rebuild: bool = False,
        limit: int = 0,
        resume: bool = True,
        save_interval: int = 1000
    ):
        """
        构建向量索引

        Args:
            force_rebuild: 是否强制重建（即使已有缓存）
            limit: 最多构建多少条标准，0表示不限制
            resume: 是否从断点继续（如果有未完成的构建）
            save_interval: 增量保存间隔（每处理多少条保存一次）
        """
        # 检查是否正在构建
        if self.build_status["is_building"]:
            logger.warning("向量索引正在构建中，请勿重复调用")
            return

        try:
            self.build_status["is_building"] = True
            self.build_status["error"] = None

            # 确保维度已确定
            await self._ensure_dimension()

            # 如果不强制重建，尝试加载缓存
            if not force_rebuild and self._load_from_disk():
                logger.info(f"从缓存加载向量索引，共 {len(self.metadata)} 条标准")
                self.build_status["is_building"] = False
                return

            logger.info(f"开始构建标准向量索引（限制：{limit if limit > 0 else '不限制'}）...")
            logger.info(f"Embedding 提供商: {self._embedding_provider.provider_name}, "
                        f"模型: {self._embedding_provider.model_name}, 维度: {self.dimension}")

            # 从数据库加载标准
            if limit > 0:
                standards = await StandardBaseInfo.all().limit(limit)
            else:
                standards = await StandardBaseInfo.all()

            logger.info(f"从数据库加载了 {len(standards)} 条标准")

            if len(standards) == 0:
                logger.warning("数据库中没有标准数据")
                self.build_status["is_building"] = False
                return

            # 检查是否从断点恢复
            start_index = 0
            if resume and not force_rebuild:
                progress = self._load_progress()
                if progress and progress.get("last_processed_id"):
                    # 找到上次处理到的位置
                    last_id = progress["last_processed_id"]
                    for i, std in enumerate(standards):
                        if std.id == last_id:
                            start_index = i + 1
                            logger.info(f"从断点恢复，跳过前 {start_index} 条标准")
                            # 加载已有的数据（跳过兼容性校验，因为断点恢复时 provider 不变）
                            self._load_from_disk_raw()
                            break

            # 初始化 FAISS 索引（如果还没有）
            if self.index is None:
                import faiss
                self.index = faiss.IndexFlatL2(self.dimension)
                self.metadata = []

            # 批量处理
            self.build_status["total"] = len(standards)
            self.build_status["processed"] = start_index

            for i in range(start_index, len(standards), save_interval):
                batch_standards = standards[i:i + save_interval]

                # 准备批次文本
                texts = []
                batch_metadata = []

                for std in batch_standards:
                    text = f"标准名称：{std.cname or ''}\n适用范围：{std.use_range or ''}"
                    texts.append(text)
                    batch_metadata.append({
                        "id": std.id,
                        "cname": std.cname,
                        "use_range": std.use_range,
                        "standard_no": std.standard_no,
                    })

                # 向量化当前批次
                logger.info(f"向量化进度：{i}/{len(standards)}")
                embeddings = await self._embedding_provider.embed_texts(texts)

                # 添加到索引
                self.index.add(np.array(embeddings).astype('float32'))
                self.metadata.extend(batch_metadata)

                # 更新进度
                self.build_status["processed"] = i + len(batch_standards)
                self.build_status["last_processed_id"] = batch_standards[-1].id

                # 增量保存
                self._save_to_disk()
                self._save_progress()

                logger.info(f"已保存进度：{self.build_status['processed']}/{len(standards)}")

            logger.info(f"向量索引构建完成，共 {len(self.metadata)} 条标准")

            # 清除进度文件
            self._clear_progress()

        except Exception as e:
            logger.error(f"构建向量索引失败: {e}")
            self.build_status["error"] = str(e)
            raise

        finally:
            self.build_status["is_building"] = False

    async def search_similar(
        self,
        query_name: str,
        query_use_range: Optional[str] = None,
        top_k: int = 10,
        exclude_no: Optional[str] = None
    ) -> List[Tuple[Dict, float]]:
        """
        搜索相似标准

        Args:
            query_name: 查询的标准名称
            query_use_range: 查询的适用范围
            top_k: 返回前K个结果
            exclude_no: 要排除的标准编号（通常是待查重标准自己的编号）

        Returns:
            [(metadata, similarity_score), ...]
        """
        # 确保维度已确定
        await self._ensure_dimension()

        # 懒加载：如果索引未初始化，尝试从缓存加载
        if self.index is None:
            logger.info("向量索引未加载，尝试从缓存加载...")
            if self._load_from_disk():
                logger.info(f"成功从缓存加载向量索引，共 {len(self.metadata)} 条标准")
            else:
                logger.warning("向量索引未初始化，且缓存不存在。自动构建索引中...")
                await self.build_index()
                if self.index is None:
                    logger.error("自动构建索引失败")
                    return []
                logger.info(f"自动构建索引完成，共 {len(self.metadata)} 条标准")

        # 组合查询文本
        query_text = f"标准名称：{query_name}\n适用范围：{query_use_range or ''}"

        # 向量化查询
        try:
            query_embedding = await self._embedding_provider.embed_query(query_text)
        except Exception as e:
            logger.error(f"查询向量化出错: {e}")
            return []

        # FAISS 检索（多检索一些，因为可能需要过滤）
        search_k = top_k * 2 if exclude_no else top_k
        query_vector = np.array([query_embedding]).astype('float32')
        distances, indices = await asyncio.to_thread(self.index.search, query_vector, search_k)

        # 组装结果（距离越小越相似，转为相似度分数）
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                metadata = self.metadata[idx]

                # 过滤掉相同标准编号的结果
                if exclude_no and metadata.get("standard_no") == exclude_no:
                    logger.info(f"过滤掉自身：{metadata.get('cname')} (编号: {exclude_no})")
                    continue

                # 将 L2 距离转为相似度分数 (0-1)
                # L2距离范围很大，这里简单转换
                similarity_score = 1.0 / (1.0 + float(distance))
                results.append((metadata, similarity_score))

                # 达到所需数量就停止
                if len(results) >= top_k:
                    break

        return results

    async def batch_search_similar(
        self,
        queries: List[Tuple[str, Optional[str], Optional[str]]],
        top_k: int = 10,
        allowed_standard_nos: Optional[List[str]] = None
    ) -> List[List[Tuple[Dict, float]]]:
        """
        批量搜索相似标准（优化版本）

        Args:
            queries: 查询列表，每项为 (query_name, query_use_range, exclude_no)
            top_k: 每个查询返回前K个结果
            allowed_standard_nos: 允许的标准编号列表（用于查重池限制），如果为None则不限制

        Returns:
            List[List[(metadata, similarity_score), ...]]
        """
        # 确保维度已确定
        await self._ensure_dimension()

        # 懒加载：如果索引未初始化，尝试从缓存加载
        if self.index is None:
            logger.info("向量索引未加载，尝试从缓存加载...")
            if self._load_from_disk():
                logger.info(f"成功从缓存加载向量索引，共 {len(self.metadata)} 条标准")
            else:
                logger.warning("向量索引未初始化，且缓存不存在。自动构建索引中...")
                await self.build_index()
                if self.index is None:
                    logger.error("自动构建索引失败")
                    return [[] for _ in queries]
                logger.info(f"自动构建索引完成，共 {len(self.metadata)} 条标准")

        # 每次都检查并同步数据库
        logger.info("检查数据库是否有更新...")
        try:
            result = await self.sync_with_database(auto_cleanup=True)
            if result.get("success"):
                added = result.get('added', 0)
                removed = result.get('removed', 0)
                updated = result.get('updated', 0)
                if added > 0 or removed > 0 or updated > 0:
                    logger.info(f"数据库同步完成：添加 {added}，删除 {removed}，更新 {updated}")
                else:
                    logger.info("数据库无变化")
        except Exception as e:
            logger.error(f"数据库同步失败: {e}，继续使用现有索引")

        # 组合所有查询文本（限制长度在2048字符以内）
        query_texts = []
        for name, use_range, _ in queries:
            # 构建查询文本
            text = f"标准名称：{name}\n适用范围：{use_range or ''}"

            # 确保文本长度在 [1, 2048] 范围内
            if len(text) > 2048:
                text = text[:2048]
            elif len(text) == 0:
                text = "标准名称：未知"

            query_texts.append(text)

        # 批量向量化查询
        try:
            all_embeddings = await self._embedding_provider.embed_texts(query_texts)
        except Exception as e:
            logger.error(f"批量查询向量化出错: {e}")
            return [[] for _ in queries]

        # 批量 FAISS 检索
        query_vectors = np.array(all_embeddings).astype('float32')

        # 如果有查重池限制，增加检索数量以确保有足够的结果
        if allowed_standard_nos:
            search_k = min(len(self.metadata), top_k * 10)  # 多检索一些用于过滤
        else:
            search_k = top_k * 2  # 多检索一些用于过滤

        distances_batch, indices_batch = await asyncio.to_thread(self.index.search, query_vectors, search_k)

        # 组装每个查询的结果
        all_results = []
        for query_idx, (query_name, query_use_range, exclude_no) in enumerate(queries):
            distances = distances_batch[query_idx]
            indices = indices_batch[query_idx]

            results = []
            for idx, distance in zip(indices, distances):
                if idx < len(self.metadata):
                    metadata = self.metadata[idx]

                    # 跳过已删除的条目
                    if metadata.get("_deleted"):
                        continue

                    # 过滤掉相同标准编号的结果
                    if exclude_no and metadata.get("standard_no") == exclude_no:
                        continue

                    # 如果指定了查重池，只保留池中的标准
                    if allowed_standard_nos is not None:
                        if metadata.get("standard_no") not in allowed_standard_nos:
                            continue

                    # 将 L2 距离转为相似度分数 (0-1)
                    similarity_score = 1.0 / (1.0 + float(distance))
                    results.append((metadata, similarity_score))

                    # 达到所需数量就停止
                    if len(results) >= top_k:
                        break

            all_results.append(results)

        return all_results

    # ==================== 索引元数据管理 ====================

    def _save_index_meta(self):
        """保存索引元数据（记录 embedding provider/model/dimension 信息）"""
        try:
            os.makedirs("data", exist_ok=True)
            meta = self._embedding_provider.get_metadata()
            meta["created_at"] = datetime.now().isoformat()
            meta["record_count"] = len(self.metadata)

            with open(self.index_meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            logger.info(f"索引元数据已保存: {meta}")
        except Exception as e:
            logger.error(f"保存索引元数据失败: {e}")

    def _load_index_meta(self) -> Optional[Dict]:
        """加载索引元数据"""
        try:
            if os.path.exists(self.index_meta_file):
                with open(self.index_meta_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载索引元数据失败: {e}")
        return None

    def _validate_index_compatibility(self) -> bool:
        """
        校验已保存的索引是否与当前 Embedding Provider 兼容

        校验规则：
        1. 如果没有索引元数据文件（旧索引），尝试向后兼容
        2. 如果 provider / model / dimension 不匹配，视为不兼容

        Returns:
            True 表示兼容可用，False 表示需要重建
        """
        saved_meta = self._load_index_meta()
        current_meta = self._embedding_provider.get_metadata()

        if saved_meta is None:
            # 向后兼容：旧索引没有元数据文件
            if (current_meta["embedding_provider"] == "dashscope"
                    and current_meta["embedding_model"] == "text-embedding-v2"
                    and current_meta["embedding_dimension"] == 1536):
                logger.info("旧索引无元数据，当前配置匹配默认值（DashScope v2 1536维），视为兼容")
                # 补写元数据文件
                self._save_index_meta()
                return True
            else:
                logger.warning("旧索引无元数据且当前配置非默认值，需要重建")
                return False

        # 逐项比对
        checks = [
            ("embedding_provider", saved_meta.get("embedding_provider"), current_meta["embedding_provider"]),
            ("embedding_model", saved_meta.get("embedding_model"), current_meta["embedding_model"]),
            ("embedding_dimension", saved_meta.get("embedding_dimension"), current_meta["embedding_dimension"]),
        ]

        for field, saved_val, current_val in checks:
            if saved_val != current_val:
                logger.warning(
                    f"索引不兼容: {field} 不匹配 "
                    f"(已保存={saved_val}, 当前={current_val})"
                )
                return False

        logger.info("索引兼容性校验通过")
        return True

    def _clear_incompatible_index(self):
        """备份并清除不兼容的索引文件"""
        backup_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        for filepath in [self.index_file, self.metadata_file, self.index_meta_file]:
            if os.path.exists(filepath):
                backup_path = f"{filepath}.bak_{backup_suffix}"
                shutil.copy2(filepath, backup_path)
                os.remove(filepath)
                logger.info(f"已备份并移除不兼容索引文件: {filepath} -> {backup_path}")

    # ==================== 磁盘读写 ====================

    def _save_to_disk(self):
        """保存索引到磁盘"""
        try:
            os.makedirs("data", exist_ok=True)

            # 保存 FAISS 索引（使用 reconstruct_n 获取所有向量）
            import faiss
            if self.index is not None and self.index.ntotal > 0:
                vectors = np.zeros((self.index.ntotal, self.dimension), dtype='float32')
                for i in range(self.index.ntotal):
                    vectors[i] = self.index.reconstruct(i)
                np.save(self.index_file, vectors)
            else:
                logger.warning("索引为空，跳过保存")
                return

            # 保存元数据
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)

            # 保存索引元数据
            self._save_index_meta()

            logger.info(f"向量索引已保存到磁盘: {self.index_file}, {self.metadata_file}")

        except Exception as e:
            logger.error(f"保存索引失败: {e}")

    def _load_from_disk(self) -> bool:
        """从磁盘加载索引（含兼容性校验）"""
        try:
            if not os.path.exists(self.index_file) or not os.path.exists(self.metadata_file):
                return False

            # 兼容性校验
            if not self._validate_index_compatibility():
                logger.warning("索引与当前 Embedding Provider 不兼容，将触发重建")
                self._clear_incompatible_index()
                return False

            # 加载 FAISS 索引
            import faiss
            vectors = np.load(self.index_file)

            # 从实际数据中校验维度
            actual_dimension = vectors.shape[1]
            if actual_dimension != self.dimension:
                logger.error(
                    f"向量维度不匹配: 文件={actual_dimension}, "
                    f"配置={self.dimension}，索引将被重建"
                )
                self._clear_incompatible_index()
                return False

            self.index = faiss.IndexFlatL2(self.dimension)
            self.index.add(vectors.astype('float32'))

            # 加载元数据
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)

            return True

        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return False

    def _load_from_disk_raw(self) -> bool:
        """从磁盘加载索引（跳过兼容性校验，用于断点恢复）"""
        try:
            if not os.path.exists(self.index_file) or not os.path.exists(self.metadata_file):
                return False

            import faiss
            vectors = np.load(self.index_file)
            self.index = faiss.IndexFlatL2(self.dimension)
            self.index.add(vectors.astype('float32'))

            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)

            return True
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return False

    # ==================== 构建进度管理 ====================

    def _save_progress(self):
        """保存构建进度"""
        try:
            os.makedirs("data", exist_ok=True)
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.build_status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存进度失败: {e}")

    def _load_progress(self) -> Optional[Dict]:
        """加载构建进度"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载进度失败: {e}")
        return None

    def _clear_progress(self):
        """清除进度文件"""
        try:
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
                logger.info("构建进度已清除")
        except Exception as e:
            logger.error(f"清除进度失败: {e}")

    # ==================== 状态与统计 ====================

    def get_build_status(self) -> Dict:
        """获取构建状态"""
        return {
            **self.build_status,
            "progress_percentage": (
                self.build_status["processed"] / self.build_status["total"] * 100
                if self.build_status["total"] > 0 else 0
            )
        }

    def get_stats(self) -> Dict:
        """获取向量库统计信息"""
        return {
            "total_standards": len(self.metadata),
            "index_size": self.index.ntotal if self.index else 0,
            "dimension": self.dimension,
            "embedding_provider": self._embedding_provider.provider_name,
            "embedding_model": self._embedding_provider.model_name,
            "cache_exists": os.path.exists(self.index_file),
            "build_status": self.get_build_status()
        }

    # ==================== 索引维护 ====================

    def _find_standard_index(self, standard_no: str) -> Optional[int]:
        """
        查找标准在 metadata 中的索引位置

        Args:
            standard_no: 标准编号

        Returns:
            索引位置，如果不存在则返回 None
        """
        for i, meta in enumerate(self.metadata):
            if not meta.get("_deleted") and meta.get("standard_no") == standard_no:
                return i
        return None

    async def cleanup_deleted(self) -> bool:
        """
        清理已删除的条目，重建索引

        Returns:
            是否成功
        """
        try:
            # 确保索引已加载
            if self.index is None:
                logger.info("向量索引未加载，尝试从缓存加载...")
                if not self._load_from_disk():
                    logger.error("向量索引未初始化，请先构建索引")
                    return False

            # 统计删除的条目
            deleted_count = sum(1 for m in self.metadata if m.get("_deleted"))
            if deleted_count == 0:
                logger.info("没有需要清理的条目")
                return True

            logger.info(f"开始清理 {deleted_count} 个已删除条目...")

            # 重建索引（只保留未删除的）
            import faiss
            new_index = faiss.IndexFlatL2(self.dimension)
            new_metadata = []

            # 从旧索引中重建
            for i, meta in enumerate(self.metadata):
                if not meta.get("_deleted"):
                    # 获取向量
                    vector = self.index.reconstruct(i)
                    new_index.add(np.array([vector]).astype('float32'))
                    # 清理元数据中的删除标记
                    clean_meta = {k: v for k, v in meta.items() if k != "_deleted"}
                    new_metadata.append(clean_meta)

            # 更新索引
            self.index = new_index
            self.metadata = new_metadata

            # 保存到磁盘
            self._save_to_disk()

            logger.info(f"清理完成，移除了 {deleted_count} 个条目，当前索引包含 {len(self.metadata)} 个标准")
            return True

        except Exception as e:
            logger.error(f"清理失败: {e}")
            return False

    async def sync_with_database(self, auto_cleanup: bool = True) -> Dict[str, any]:
        """
        自动同步向量索引与数据库

        对比 standard_base_info 表和向量索引，自动维护索引

        Args:
            auto_cleanup: 如果删除条目较多，是否自动清理

        Returns:
            同步结果统计
        """
        try:
            # 确保索引已加载
            if self.index is None:
                logger.info("向量索引未加载，尝试从缓存加载...")
                if not self._load_from_disk():
                    logger.warning("向量索引未初始化，将进行全量构建")
                    await self.build_index()
                    return {
                        "success": True,
                        "action": "full_rebuild",
                        "message": "索引未初始化，已执行全量构建"
                    }

            logger.info("开始同步向量索引与数据库...")

            # 1. 从数据库加载所有标准
            db_standards = await StandardBaseInfo.all()
            db_standard_dict = {std.standard_no: std for std in db_standards}
            db_standard_nos = set(db_standard_dict.keys())

            logger.info(f"数据库中有 {len(db_standards)} 个标准")

            # 2. 从索引中获取所有标准编号（未删除的）
            index_standard_dict = {}
            for i, meta in enumerate(self.metadata):
                if not meta.get("_deleted"):
                    std_no = meta.get("standard_no")
                    if std_no:
                        index_standard_dict[std_no] = (i, meta)

            index_standard_nos = set(index_standard_dict.keys())
            logger.info(f"索引中有 {len(index_standard_nos)} 个标准（未删除）")

            # 3. 找出差异
            to_add = db_standard_nos - index_standard_nos
            to_remove = index_standard_nos - db_standard_nos
            common = db_standard_nos & index_standard_nos

            logger.info(f"差异统计：待添加 {len(to_add)}，待删除 {len(to_remove)}，需检查更新 {len(common)}")

            # 4. 执行同步
            added_count = 0
            removed_count = 0
            updated_count = 0
            failed_count = 0

            # 4.1 批量添加新标准
            if to_add:
                logger.info(f"批量添加 {len(to_add)} 个新标准到索引...")
                try:
                    texts = []
                    standards_to_add = []
                    for std_no in to_add:
                        standard = db_standard_dict[std_no]
                        text = f"标准名称：{standard.cname or ''}\n适用范围：{standard.use_range or ''}"
                        texts.append(text)
                        standards_to_add.append(standard)

                    embeddings = await self._embedding_provider.embed_texts(texts)

                    self.index.add(np.array(embeddings).astype('float32'))
                    for standard in standards_to_add:
                        self.metadata.append({
                            "id": standard.id,
                            "cname": standard.cname,
                            "use_range": standard.use_range,
                            "standard_no": standard.standard_no,
                        })
                    added_count = len(standards_to_add)

                except Exception as e:
                    logger.error(f"批量添加标准失败: {e}")
                    failed_count += len(to_add)

            # 4.2 删除不存在的标准
            if to_remove:
                logger.info(f"标记删除 {len(to_remove)} 个不存在的标准...")
                for std_no in to_remove:
                    idx, meta = index_standard_dict[std_no]
                    self.metadata[idx]["_deleted"] = True
                    removed_count += 1

            # 4.3 检查需要更新的标准（对比 cname 和 use_range）
            if common:
                logger.info(f"检查 {len(common)} 个标准是否需要更新...")
                to_update = []
                for std_no in common:
                    db_std = db_standard_dict[std_no]
                    idx, index_meta = index_standard_dict[std_no]

                    if (db_std.cname != index_meta.get("cname") or
                        db_std.use_range != index_meta.get("use_range")):
                        to_update.append((std_no, idx, db_std))

                if to_update:
                    logger.info(f"批量更新 {len(to_update)} 个标准...")
                    try:
                        texts = []
                        standards_to_update = []
                        for std_no, idx, db_std in to_update:
                            self.metadata[idx]["_deleted"] = True
                            text = f"标准名称：{db_std.cname or ''}\n适用范围：{db_std.use_range or ''}"
                            texts.append(text)
                            standards_to_update.append(db_std)

                        embeddings = await self._embedding_provider.embed_texts(texts)

                        self.index.add(np.array(embeddings).astype('float32'))
                        for standard in standards_to_update:
                            self.metadata.append({
                                "id": standard.id,
                                "cname": standard.cname,
                                "use_range": standard.use_range,
                                "standard_no": standard.standard_no,
                            })
                        updated_count = len(standards_to_update)

                    except Exception as e:
                        logger.error(f"批量更新标准失败: {e}")
                        failed_count += len(to_update)

            # 5. 保存索引
            if added_count > 0 or removed_count > 0 or updated_count > 0:
                self._save_to_disk()
                logger.info(f"同步完成：添加 {added_count}，删除 {removed_count}，更新 {updated_count}，失败 {failed_count}")

            # 6. 检查是否需要清理
            deleted_count = sum(1 for m in self.metadata if m.get("_deleted"))
            need_cleanup = deleted_count > len(self.metadata) * 0.1

            if need_cleanup and auto_cleanup:
                logger.info(f"删除条目过多（{deleted_count}），自动执行清理...")
                await self.cleanup_deleted()

            return {
                "success": True,
                "added": added_count,
                "removed": removed_count,
                "updated": updated_count,
                "failed": failed_count,
                "total_in_db": len(db_standards),
                "total_in_index": len([m for m in self.metadata if not m.get("_deleted")]),
                "deleted_pending": deleted_count,
                "cleanup_executed": need_cleanup and auto_cleanup
            }

        except Exception as e:
            logger.error(f"同步失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# 全局单例
_vector_store: Optional[StandardVectorStore] = None


def get_vector_store(force_new: bool = False) -> StandardVectorStore:
    """
    获取向量库单例

    Args:
        force_new: 是否强制创建新实例（用于 provider 配置变更后重置）
    """
    global _vector_store
    if _vector_store is None or force_new:
        _vector_store = StandardVectorStore()
    return _vector_store


__all__ = ["StandardVectorStore", "get_vector_store"]
