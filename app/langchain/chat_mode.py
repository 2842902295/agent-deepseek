"""
对话模式（DB 驱动可配置）+ 用户思考强度（滑块档位，模型块驱动）。

两层数据（均在 app/models/standard/model_config.py）：
- AgentChatModeConfig：**模式清单真相源**（超管可增删改，每模式一行）。
  表为空（新装库）时 ensure_seed 播种 fast/balanced/complex 三行；既有库
  不重建任何行——balanced 也允许删除，删除接口只保底「至少留一个模式」。
  chat_block_key 为 null 的模式走兜底链（角色模型配置 → 全局激活块）。
  模块级缓存 load_modes()，config 写端点须调 invalidate_modes_cache()。
- AgentUserChatPref：每用户一行的偏好（所选模式 + 思考强度）；无行 = 默认
  balanced + 块默认档（默认不开）。

思考强度是 **wire 档位值本身**（none/low/medium/high/max），每个 chat 块的
合法档位存 agent_model_block.reasoning_levels（逗号分隔白名单，顺序即滑块序；
空 = 该模型不展示强度调节）。存量 legacy 语义 token（standard/advanced/ultimate）
读时经 LEGACY_LEVEL_MAP 翻译。运行期档位编码进 llm 回环代理 URL 路径段
（/llm-proxy/{block}/{level}，见 llm_proxy.py，按块 levels 白名单校验），由代理
在 wire 上按 provider 翻译注入（_build_thinking_extra_body：qwen/deepseek/GLM
透传 reasoning_effort、kimi none→minimal 地板、binary 协议 provider 降级为开/关）。

**默认与迁移规则**：
- 默认不开：块 reasoning_default 显式配置（且 ∈ levels）优先；否则 levels 含
  none → none，再否则最低档（kimi 恒开思考 → low）。
- 平滑迁移：已存档位 ∉ 当前有效块 levels 时（切模式/切模型/legacy），不回退
  默认，按序数 none<low<medium<high<max 就近映射到相邻档位（平手取低档，
  费用保守）；存量值不改写，切回原块自动还原。

消费口：
- qa.py::_get_agent_for_session / scheduler.py / 每日简报 —— resolve_user_chat_pref
  拿 (mode, block, level)，块非空时覆盖 profile.chat_block_key（须在
  set_current_profile 之前 replace，保证 effective_chat_supports_vision 一致），
  level 经 create_qa_agent(thinking_level=...) 进 agent 的 proxy baseURL。
  调用方应传 fallback_block=profile.chat_block_key（角色配置块）供 level 归一。
- user_id=None（匿名/系统路径）完全不干预：返回 ("", None, None)，
  走旧 proxy 路由 + env thinking 逻辑，现状不变。

语义边界：块级 agent_model_block.thinking 开关在带 level 的 dsh 对话路径被
用户强度接管（level 优先于 env），仅系统直连路径（_build_chat）与无 levels
块（level=None）仍读它。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# 默认模式：新装库播种基线 + pref 缺失/失效时的首选回落值（balanced 仍在清单则回落它，
# 已被超管删除则回落排序首行——见 fallback_mode；删除接口保底至少留一行）
DEFAULT_MODE = "balanced"

# wire 档位值 → 中文标签（pref.thinking_level / URL 路径段 / 滑块 marks 共用同一套值）
LEVEL_LABELS: dict[str, str] = {"none": "关闭", "low": "轻度", "medium": "中等", "high": "高", "max": "极致"}

# 存量 legacy 语义 token → wire 值（旧三档方案 pref 里存的值，读时翻译后走平滑迁移）
LEGACY_LEVEL_MAP: dict[str, str] = {"standard": "none", "advanced": "medium", "ultimate": "high"}

# 平滑迁移序数（minimal 是 kimi 翻译层地板值，与 low 同序，仅当块显式配置它时参与）
_LEVEL_ORD: dict[str, int] = {"none": 0, "minimal": 1, "low": 1, "medium": 2, "high": 3, "max": 4}

# 播种基线（mode, label, note, sort_order）：仅新装库（表为空）首启插入；
# 展示序从强到弱（复杂 → 均衡 → 快速），既有库仅回填空 label
_SEED_MODES: tuple[tuple[str, str, str, int], ...] = (
    ("complex", "复杂", "深度推理，适合复杂任务", 0),
    ("balanced", "均衡", "默认推荐，兼顾质量与速度", 1),
    ("fast", "快速", "日常快速问答，响应最迅速", 2),
)

# load_modes 的模块级缓存（单 worker 进程内共享；config 写端点 invalidate）
_modes_cache: Optional[list[Any]] = None


async def load_modes() -> list[Any]:
    """模式行清单（sort_order, id 升序 = 前端展示序），带模块级缓存。"""
    global _modes_cache
    if _modes_cache is None:
        from app.models.standard import AgentChatModeConfig

        _modes_cache = list(await AgentChatModeConfig.all().order_by("sort_order", "id"))
    return _modes_cache


def invalidate_modes_cache() -> None:
    """模式配置写后失效缓存（所有 config 写端点必须调用）。"""
    global _modes_cache
    _modes_cache = None


def mode_label(row: Any) -> str:
    """模式显示名：label 空回退 key。"""
    return (getattr(row, "label", None) or getattr(row, "mode", "")) or ""


def fallback_mode(rows: list[Any]) -> str:
    """pref 缺失 / 指向被删模式时的回落 key：balanced 仍在清单则它，
    否则取排序首行（rows 须已按 sort_order,id 排好，即 load_modes 原序）。"""
    keys = {getattr(r, "mode", None) for r in rows}
    if DEFAULT_MODE in keys:
        return DEFAULT_MODE
    return getattr(rows[0], "mode", DEFAULT_MODE) if rows else DEFAULT_MODE


async def ensure_seed() -> None:
    """新装库播种：**仅当表为空**时插入 complex/balanced/fast 三行（从强到弱排序）。

    既有库不重建任何行——balanced 也允许被超管删除（删除接口保底「至少留一个」），
    pref 指向被删模式由 fallback_mode 回落。既有行只在 label 为空且命中播种
    key 时回填 label/note/sort_order（不覆盖超管改名）。
    调用点在 lifespan，失败由调用方 try/except 吞掉不阻塞启动。
    """
    from app.models.standard import AgentChatModeConfig

    existing = list(await AgentChatModeConfig.all())
    if not existing:
        for mode, label, note, sort in _SEED_MODES:
            await AgentChatModeConfig.create(
                mode=mode, label=label, note=note, sort_order=sort, chat_block_key=None
            )
    else:
        seed_map = {m: (label, note, sort) for m, label, note, sort in _SEED_MODES}
        for row in existing:
            if not row.label and row.mode in seed_map:
                label, note, sort = seed_map[row.mode]
                row.label, row.note, row.sort_order = label, note, sort
                await row.save(update_fields=["label", "note", "sort_order"])
    invalidate_modes_cache()


def block_default_level(block_key: Optional[str]) -> Optional[str]:
    """块默认档（默认不开规则）：显式 reasoning_default（∈ levels）优先；
    否则 levels 含 none → none；否则最低档（levels 首项，配置序 = 滑块序）。
    块无 levels → None（不展示强度调节，走 env 旧路由）。"""
    from app.langchain.model_selection import block_reasoning_default, block_reasoning_levels

    levels = block_reasoning_levels(block_key)
    if not levels:
        return None
    d = block_reasoning_default(block_key)
    if d and d in levels:
        return d
    if "none" in levels:
        return "none"
    return levels[0]


def migrate_level(level: str, levels: list[str]) -> Optional[str]:
    """平滑迁移：把档位按序数就近映射进块 levels 白名单（平手取低档，费用保守）。

    例：medium → kimi{low,high,max} 落 low；none → kimi 落 low；
    medium → deepseekpro{high,max} 落 high。levels 空或档位不可识别 → None。
    """
    if not levels or level not in _LEVEL_ORD:
        return None
    src = _LEVEL_ORD[level]
    return min(levels, key=lambda lv: (abs(_LEVEL_ORD.get(lv, src) - src), _LEVEL_ORD.get(lv, 0)))


def normalize_level(raw: Optional[str], block_key: Optional[str]) -> Optional[str]:
    """pref 原始值 → 块合法档位（读取口径统一入口）。

    - 块无 levels → None（走 env CHAT_THINKING 旧路由，URL 不带档位段）。
    - 空值 → 块默认档（block_default_level，默认不开）。
    - legacy token 先翻译；∈ levels 原样；∉ levels 就近迁移；不可识别脏值 → 块默认档。
    """
    from app.langchain.model_selection import block_reasoning_levels

    levels = block_reasoning_levels(block_key)
    if not levels:
        return None
    val = (raw or "").strip()
    if val:
        val = LEGACY_LEVEL_MAP.get(val, val)
        if val in levels:
            return val
        migrated = migrate_level(val, levels)
        if migrated:
            return migrated
    return block_default_level(block_key)


async def resolve_user_chat_pref(
    user_id: Optional[int], fallback_block: Optional[str] = None
) -> tuple[str, Optional[str], Optional[str]]:
    """解析用户的对话模式偏好 → (mode, chat_block_key|None, level|None)。

    - user_id=None（匿名/系统路径）→ ("", None, None)：完全不干预，现状不变。
    - mode：pref 值须 ∈ DB 模式行（load_modes），否则回落 fallback_mode
      （balanced 优先、被删则排序首行；pref 指向被删模式自动回落，无需清数据）。
    - block：仅当模式行块非空且定义完整（_block_configured）才返回；否则 None，
      由调用方走兜底链（角色 profile → 全局激活块）。**调用方应把兜底链上
      自己的块（profile.chat_block_key）经 fallback_block 传入**，供 level 归一
      对准真实生效块（qa.py / scheduler.py 已如此）。
    - level：对「有效块」（模式块 → fallback_block → 全局激活块）调
      normalize_level——块无 levels 时返回 None（agent 走旧 env 路由）。
    - 偏好行里的非法值（历史脏数据）按默认/迁移处理，不抛错。
    """
    if user_id is None:
        return "", None, None

    from app.langchain.config import get_active_block
    from app.langchain.model_selection import DEFAULT_BLOCKS, _block_configured
    from app.models.standard import AgentUserChatPref

    pref = await AgentUserChatPref.get_or_none(user_id=user_id)
    row_list = await load_modes()
    rows = {r.mode: r for r in row_list}
    mode = (pref.mode if pref else None) or DEFAULT_MODE
    if mode not in rows:
        mode = fallback_mode(row_list)

    cfg = rows.get(mode)
    block: Optional[str] = None
    if cfg and cfg.chat_block_key:
        if _block_configured("chat", cfg.chat_block_key):
            block = cfg.chat_block_key
        else:
            logger.warning(f"[chat_mode] 模式 {mode} 配置的块 {cfg.chat_block_key} 不可用，走兜底链")

    # level 归一要对准「真实生效块」：模式块 → 调用方兜底块 → 全局激活块
    eff_block = block
    if not eff_block:
        eff_block = fallback_block if (fallback_block and _block_configured("chat", fallback_block)) else None
    if not eff_block:
        eff_block = get_active_block("chat", DEFAULT_BLOCKS["chat"])
    level = normalize_level(pref.thinking_level if pref else None, eff_block)

    return mode, block, level
