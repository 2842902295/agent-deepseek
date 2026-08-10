"""
模型切换选择逻辑（超管全局切换）——DB 驱动版。

- AgentModelBlock 表 = 预设块的**运行时真相源**：chat/image/video 三类的清单、凭据、模型属性
- 启动 / 重载流程：播种（可选）→ 读库进内存 → **物化进 os.environ** → 清缓存
  「env 物化」把 DB 块定义写成 {BLOCK}_BASE_URL/API_KEY/MODEL/... 环境变量，
  load_role / load_provider / has_role / has_capability 等全部消费方因此零改动
- 播种：启动时把代码基线 _SEED_PRESETS 幂等插入表中（含软删除墓碑检测，删过的块不复活）。
  代码基线不含任何真实密钥——凭据在播种 / 基线物化时从环境变量 {BLOCK}_API_KEY 补齐
  （见 _resolve_seed_presets）；新装库 / 生产部署零手工 SQL
- AgentModelConfig 仍只存「每类别选了哪个块」（3 行）
- clear_model_caches 切换后清掉 4 层缓存，使新模型对所有用户立即生效
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

from app.langchain.config import (
    _load_provider_block,
    _load_provider_default,
    get_active_block,
    load_role,
    set_active_block,
)

# 类别 → 默认块名（首启 / 未配置 / 加载失败时的兜底）
DEFAULT_BLOCKS: dict[str, str] = {"chat": "CHAT_DASHSCOPE", "image": "IMAGE", "video": "VIDEO_APIPOD"}

SUPPORTED_CATEGORIES: tuple[str, ...] = ("chat", "image", "video")

# 生成类能力的 provider 白名单（每个对应一个已实现的 client；接新厂商必须先写 client 代码）
VALID_PROVIDERS: dict[str, set[str]] = {
    "image": {"gpt", "apipod", "qwen"},
    "video": {"ark", "openrouter", "apipod", "happyhorse"},
}

# ── 播种基线（代码内维护，首启自动插入 DB）──────────────────────────────────
# 新装库首次启动时把这份清单幂等插入 agent_model_block（已存在的 block_key 一律跳过，
# 含软删墓碑）。既有库里 DB 是唯一真相源——换 key / 改模型 / 加块都走 DB（admin 子 agent），
# 不需要同步改这里；这里只决定「全新安装的库初始长什么样」。
# ⚠️ 凭据红线：api_key 一律留空，真实密钥只存在 .env 的 {BLOCK}_API_KEY 里，
# 由 _resolve_seed_presets() 在播种 / 基线物化时补齐（代码永不落明文密钥）。
_SEED_PRESETS: list[dict[str, Any]] = [
    {
        "block_key": "CHAT_GROK",
        "category": "chat",
        "label": "QuickRouter · Grok-4.5",
        "provider": None,
        "base_url": "https://api.quickrouter.ai/v1",
        "api_key": "",  # 凭据来自 .env CHAT_GROK_API_KEY（_resolve_seed_presets 补齐）
        "model": "grok-4.5",
        "vision_supported": True,
        "thinking": None,
        "context_window": None,
        "sort_order": 0,
    },
    {
        "block_key": "CHAT_DASHSCOPE",
        "category": "chat",
        "label": "百炼 · Qwen3.8-Max",
        "provider": None,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",  # 凭据来自 .env（_resolve_seed_presets 补齐，同厂商块共用一个 key）
        "model": "qwen3.8-max",
        "vision_supported": True,
        "thinking": None,
        "context_window": None,
        "sort_order": 1,
    },
    {
        "block_key": "CHAT_DEEPSEEK",
        "category": "chat",
        "label": "百炼 · DeepSeek-V4-Flash",
        "provider": None,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",  # 凭据来自 .env（_resolve_seed_presets 补齐，同厂商块共用一个 key）
        "model": "deepseek-v4-flash-0731",
        "vision_supported": False,
        "thinking": None,
        "context_window": None,
        "sort_order": 2,
    },
    {
        "block_key": "CHAT_KIMI",
        "category": "chat",
        "label": "百炼 · Kimi-K3",
        "provider": None,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",  # 凭据来自 .env（_resolve_seed_presets 补齐，同厂商块共用一个 key）
        "model": "kimi/kimi-k3",
        "vision_supported": False,
        "thinking": None,
        "context_window": None,
        "sort_order": 3,
    },
    {
        "block_key": "CHAT_N1N",
        "category": "chat",
        "label": "n1n · Claude-Sonnet-4.6",
        "provider": None,
        "base_url": "https://api.n1n.ai/v1",
        "api_key": "",  # 凭据来自 .env CHAT_N1N_API_KEY（_resolve_seed_presets 补齐）
        "model": "claude-sonnet-4-6",
        "vision_supported": True,
        "thinking": True,
        "context_window": None,
        "sort_order": 4,
    },
    {
        "block_key": "IMAGE",
        "category": "image",
        "label": "GPT-image-2（APIPod）",
        "provider": "apipod",
        "base_url": None,
        "api_key": "",  # 凭据来自 .env IMAGE_API_KEY（_resolve_seed_presets 补齐）
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 5,
    },
    {
        "block_key": "IMAGE_QWEN",
        "category": "image",
        "label": "千问 Qwen-Image-2.0",
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "api_key": "",  # 凭据来自 .env（_resolve_seed_presets 补齐，同厂商块共用一个 key）
        "model": "qwen-image-2.0-pro",
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 6,
    },
    {
        "block_key": "VIDEO_APIPOD",
        "category": "video",
        "label": "Grok Imagine 1.5（图生视频）",
        "provider": "apipod",
        "base_url": "https://api.apipod.ai/v1",
        "api_key": "",  # 凭据来自 .env VIDEO_APIPOD_API_KEY（_resolve_seed_presets 补齐）
        "model": "grok-imagine-1.5-preview",
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 7,
    },
    {
        "block_key": "VIDEO",
        "category": "video",
        "label": "火山 Seedance 2.0",
        "provider": "ark",
        "base_url": None,
        "api_key": "",  # 凭据来自 .env VIDEO_API_KEY（_resolve_seed_presets 补齐）
        "model": "doubao-seedance-2-0-260128",
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 8,
    },
    {
        "block_key": "VIDEO_OPENROUTER",
        "category": "video",
        "label": "OpenRouter · Seedance 2.0",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",  # 凭据来自 .env VIDEO_OPENROUTER_API_KEY（_resolve_seed_presets 补齐）
        "model": "bytedance/seedance-2.0-fast",
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 9,
    },
    {
        "block_key": "VIDEO_HAPPYHORSE",
        "category": "video",
        "label": "阿里云 HappyHorse",
        "provider": "happyhorse",
        "base_url": None,
        "api_key": "",  # 凭据来自 .env（_resolve_seed_presets 补齐，同厂商块共用一个 key）
        "model": None,
        "vision_supported": None,
        "thinking": None,
        "context_window": None,
        "sort_order": 10,
    },
]

# 播种基线里允许被 .env 覆盖的字段（其余字段以代码基线为准）
_SEED_ENV_FIELDS = (("base_url", "_BASE_URL"), ("api_key", "_API_KEY"), ("model", "_MODEL"), ("provider", "_PROVIDER"))


def _resolve_seed_presets() -> list[dict[str, Any]]:
    """返回「代码基线 + .env 凭据填充」后的播种清单（不改动 _SEED_PRESETS 本体）。

    代码基线不含真实密钥：api_key 一律留空，这里从环境变量 {BLOCK}_API_KEY 补齐；
    {BLOCK}_BASE_URL/_MODEL/_PROVIDER 若显式配置也可覆盖基线（便于新装库指向自建网关）。
    既有库不受影响——播种幂等跳过已存在的 block_key，运行期真相源始终是 DB。
    """
    resolved: list[dict[str, Any]] = []
    for blk in _SEED_PRESETS:
        b = dict(blk)
        for field, suf in _SEED_ENV_FIELDS:
            v = os.environ.get(f"{b['block_key']}{suf}")
            if v:
                b[field] = v
        resolved.append(b)
    return resolved


# 物化 / 清理 env 时涉及的字段后缀（chat 与 image/video 的并集）
_ENV_SUFFIXES = (
    "_BASE_URL",
    "_API_KEY",
    "_MODEL",
    "_PROVIDER",
    "_VISION_SUPPORTED",
    "_THINKING",
    "_CONTEXT_WINDOW",
    "_MAX_TOKENS",
    "_TEMPERATURE",
    "_FREQUENCY_PENALTY",
    "_PRESENCE_PENALTY",
    "_HEADERS",
)

# ── 运行时状态 ────────────────────────────────────────────────────────────────
# _blocks：DB 里未删除的块定义（启动 / 每次重载时刷新）；
# _materialized_keys：上一次物化写入过 env 的块名集合（重载时用于清理消失的块）。
_blocks: list[dict[str, Any]] = []
_materialized_keys: set[str] = set()


def _purge_block_env(block_key: str) -> None:
    """删掉某块在 env 里的全部物化变量。"""
    for suf in _ENV_SUFFIXES:
        os.environ.pop(f"{block_key}{suf}", None)


def _materialize_blocks(blocks: list[dict[str, Any]]) -> None:
    """把块定义写进 os.environ（覆盖 .env 里的同名变量），消费方零改动。

    写前逐块清空旧值：块从 DB 消失（删除）或其字段置空时，env 不残留旧数据。
    """
    global _materialized_keys
    current_keys = {b["block_key"] for b in blocks}
    for old in _materialized_keys - current_keys:
        _purge_block_env(old)
    for b in blocks:
        key = b["block_key"]
        _purge_block_env(key)
        for field, suf in (
            ("base_url", "_BASE_URL"),
            ("api_key", "_API_KEY"),
            ("model", "_MODEL"),
            ("provider", "_PROVIDER"),
        ):
            v = b.get(field)
            if v:
                os.environ[f"{key}{suf}"] = str(v)
        for field, suf in (("vision_supported", "_VISION_SUPPORTED"), ("thinking", "_THINKING")):
            v = b.get(field)
            if v is not None:
                os.environ[f"{key}{suf}"] = "true" if v else "false"
        for field, suf in (("context_window", "_CONTEXT_WINDOW"),):
            v = b.get(field)
            if v is not None:
                os.environ[f"{key}{suf}"] = str(v)
    _materialized_keys = current_keys


async def _seed_blocks() -> None:
    """把代码播种基线（_SEED_PRESETS）幂等插入 DB。已存在的 block_key 一律跳过——含软删墓碑（删过的不复活）。"""
    from app.models.standard import AgentModelBlock

    existing = {r.block_key for r in await AgentModelBlock.all()}
    created: list[str] = []
    for blk in _resolve_seed_presets():
        if blk["block_key"] in existing:
            continue
        await AgentModelBlock.create(**blk)
        created.append(blk["block_key"])
    if created:
        logger.info(f"[model_selection] 已从代码基线播种 {len(created)} 个模型块：{created}")


async def _load_and_materialize() -> None:
    """读 DB 全部未删除块进内存，并物化进 os.environ。"""
    from app.models.standard import AgentModelBlock

    global _blocks
    rows = await AgentModelBlock.filter(is_deleted=0).order_by("sort_order", "id")
    _blocks = [
        {
            "block_key": r.block_key,
            "category": (r.category or "").lower(),
            "label": r.label or r.block_key,
            "provider": (r.provider or "").lower() or None,
            "base_url": r.base_url,
            "api_key": r.api_key,
            "model": r.model,
            "vision_supported": r.vision_supported,
            "thinking": r.thinking,
            "context_window": r.context_window,
        }
        for r in rows
    ]
    _materialize_blocks(_blocks)


def _block_configured(category: str, key: str) -> bool:
    """块定义是否完整可用（chat：base_url+api_key+model；image/video：provider+api_key）。"""
    for b in _blocks:
        if b["block_key"] == key and b["category"] == category:
            if category == "chat":
                return bool(b["base_url"] and b["api_key"] and b["model"])
            return bool(b["provider"] and b["api_key"])
    return False


def available_blocks(category: str) -> list[dict[str, str]]:
    """返回某类别定义完整的预设块（[{key, label}]，供切换页 / PUT 校验用）。"""
    cat = category.lower()
    return [{"key": b["block_key"], "label": b["label"]} for b in _blocks if b["category"] == cat and _block_configured(cat, b["block_key"])]


def validate_block_changes(category: str, final_fields: dict[str, Any]) -> Optional[str]:
    """agent_model_block 写入前的字段校验（admin 写入口用）。

    final_fields 为「存量值 + 本次 changes」合并后的最终字段值（更新时只传 changes 子集会误判必填缺失）。
    返回错误原因，None 放行。
    """
    cat = (category or "").lower()
    if cat not in SUPPORTED_CATEGORIES:
        return f"category 只能是 chat / image / video，收到：{category!r}"
    provider = final_fields.get("provider")
    if cat == "chat":
        if provider:
            return "chat 块统一走 OpenAI 兼容协议，provider 必须留空"
        missing = [f for f in ("base_url", "api_key", "model") if not final_fields.get(f)]
        if missing:
            return f"chat 块必填字段缺失：{missing}"
    else:
        if not provider:
            return f"{cat} 块必须指定 provider（可选：{sorted(VALID_PROVIDERS[cat])}）"
        if str(provider).lower() not in VALID_PROVIDERS[cat]:
            return f"provider {provider!r} 无对应实现，{cat} 可选：{sorted(VALID_PROVIDERS[cat])}（接新厂商需先开发 client）"
        if not final_fields.get("api_key"):
            return f"{cat} 块必须提供 api_key"
    return None


async def load_active_blocks_from_db(seed: bool = True) -> None:
    """启动时调用：播种（可选）→ 读块 + 物化 → 种子选中配置并读进内存映射 → 清全部缓存。

    若 DB 里的选择指向不可用的块（脏数据 / 块被删），回退默认块并修正 DB，
    避免后续 load_role/load_provider 抛 ConfigError 打挂链路。
    """
    from app.models.standard import AgentModelConfig

    if seed:
        await _seed_blocks()
    await _load_and_materialize()

    for cat, default_block in DEFAULT_BLOCKS.items():
        obj, _ = await AgentModelConfig.get_or_create(
            category=cat,
            defaults={"selected_key": default_block},
        )
        block = obj.selected_key
        if not _block_configured(cat, block):
            # 首选默认块；默认块自身也不可用时，回退清单里第一个可用的预设
            fallback = default_block if _block_configured(cat, default_block) else next((p["key"] for p in available_blocks(cat)), default_block)
            logger.warning(f"[model_selection] {cat} 选中的块 {block} 不可用，回退 {fallback}")
            block = fallback
            obj.selected_key = block
            await obj.save(update_fields=["selected_key", "update_time"])
        set_active_block(cat, block)

    # 顶部 import（vector_store / seekdb 等）可能在 lifespan 之前就触发过 load_role("CHAT")
    # 甚至 get_llm()，彼时缓存的还是旧 env 值。这里统一清一次全部缓存层，
    # 确保后续走重定向 + 物化后的块。
    clear_model_caches()


async def reload_model_blocks() -> None:
    """admin 工具写 agent_model_block / agent_model_config 后的热重载钩子。

    与启动流程同一逻辑，但不再播种（防止干扰运行期数据），重载后立即清缓存生效。
    """
    await load_active_blocks_from_db(seed=False)


def clear_model_caches() -> None:
    """切换后清掉 4 层缓存，使新模型对所有用户立即生效。

    每类独立 try/except + logger.exception：单点 import 失败不会吞掉其余清理。
    EMBED / LOCAL_EMBED 单例不在此列（不参与切换）。
    """
    # ① config 的 lru_cache（load_role；load_provider 自身不缓存，其两个内部
    #    缓存体 _load_provider_default/_load_provider_block 在此清）
    try:
        load_role.cache_clear()
        _load_provider_default.cache_clear()
        _load_provider_block.cache_clear()
    except Exception:
        logger.exception("[model_selection] 清 load_role/load_provider 缓存失败")
    # ② LLM 单例（含按角色的按块单例——admin 改块凭据后同样必须失效）
    try:
        import app.langchain.llm_providers as lp

        lp._chat_singleton = None
        lp._smart_cmp_llm_singleton = None
        lp._block_llm_singletons.clear()
    except Exception:
        logger.exception("[model_selection] 清 LLM 单例失败")
    # ③ 按用户缓存的 agent 实例（冻结了 llm + 工具集，最关键）
    try:
        from app.api.v1.ai.qa import _agent_cache

        _agent_cache.clear()
    except Exception:
        logger.exception("[model_selection] 清 _agent_cache 失败")


async def get_catalog_with_current() -> dict[str, Any]:
    """返回三段清单 + 当前激活块（取运行时内存映射，即实际生效值；绝不含 api_key）。"""
    result: dict[str, Any] = {}
    for cat in ("chat", "image", "video"):
        result[cat] = {
            "options": available_blocks(cat),
            "current": get_active_block(cat, DEFAULT_BLOCKS[cat]),
        }
    return result


# 模块导入时先把代码播种基线物化进 env：保证 lifespan 之前（顶部 import 可能触发
# load_role("CHAT") 甚至 get_llm()）也有可用基线。启动后 load_active_blocks_from_db
# 会用 DB 数据（真相源）覆盖刷新；DB 与基线的差异（删块 / 换 key）由物化逻辑接管。
# 凭据经 _resolve_seed_presets 从 .env 的 {BLOCK}_API_KEY 补齐（代码基线无明文密钥）。
_materialize_blocks(_resolve_seed_presets())
