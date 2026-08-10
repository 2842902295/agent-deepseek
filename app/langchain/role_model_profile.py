"""
按角色模型配置解析（chat / 生图 / 生视频，生图生视频可禁用）。

配置表 agent_role_model_config（每角色一行；块字段三态：
null=跟随全局 / "DISABLED"=禁用(仅 image/video) / 块名）。

解析只有两层，不跨行混搭：
1. 用户角色按固定顺序 R_SUPER → R_ADMIN → R_USER → 自创角色（同 tier 按 role.id 升序）找，
   **第一个在表里有行的角色整行生效**（行内 null 字段跟随全局）；
   失效块（被删 / 凭据残缺）按该字段未配置处理、落全局，其余字段不受影响。
2. 都没命中 → OTHER 兜底行（同理整行生效）；再落全局激活块。

生效链路（两路）：
- qa.py 把 profile 编进 agent cache_key（配置/角色变化 → agent 自动重建），
  chat_block_key 透传给 create_qa_agent 构建对应块的 llm
- apply_gen_override(profile) 设 CTX_GEN_BLOCK_OVERRIDE（请求级）；config.py 的
  load_provider / has_capability 读它，IMAGE/VIDEO 全链路运行期调用点零改动跟随。
  DISABLED → has_capability 返回 False → get_image_tools()/get_video_tools()
  构建期返回 []，运行期门卫同样拒绝。

系统路径（每日简报等）不设 CTX_PROFILE / override → 自动回退全局激活块，语义不变。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.core.ctx import CTX_GEN_BLOCK_OVERRIDE
from app.langchain.config import GEN_DISABLED, chat_block_supports_vision, chat_model_supports_vision

# OTHER 是保留兜底行，不属于任何真实角色（roles 表禁建同名角色）
OTHER_ROLE_CODE = "OTHER"

# 固定优先级：tier 数字小者优先；自创角色 tier=3，同 tier 按 role.id 升序
_ROLE_ORDER: dict[str, int] = {"R_SUPER": 0, "R_ADMIN": 1, "R_USER": 2}
_CUSTOM_TIER = 3

# 同请求记忆化：业务入口解析一次并 set；运行期读取点（视觉判定等）直接读它
CTX_PROFILE: contextvars.ContextVar[Optional["RoleModelProfile"]] = contextvars.ContextVar("role_model_profile", default=None)


@dataclass(frozen=True)
class RoleModelProfile:
    """单个请求最终生效的模型配置形态。None = 跟随全局（不干预全局激活块重定向）。"""

    chat_block_key: Optional[str] = None  # None=跟随全局；否则 chat 预设块名
    image_block_key: Optional[str] = None  # None=跟随全局 | DISABLED | image 块名
    video_block_key: Optional[str] = None  # None=跟随全局 | DISABLED | video 块名
    supports_vision: bool = False  # 生效 chat 块是否原生支持图片输入

    @property
    def cache_key_part(self) -> str:
        """编进 qa.py 的 agent cache_key：配置 / 角色任一变化 → key 变 → 自动重建。"""
        return f"_m{self.chat_block_key or 'g'}_i{self.image_block_key or 'g'}_v{self.video_block_key or 'g'}"


def set_current_profile(profile: RoleModelProfile) -> None:
    """把解析结果记入当前请求上下文（供 effective_chat_supports_vision 等读取点用）。"""
    CTX_PROFILE.set(profile)


def apply_gen_override(profile: RoleModelProfile) -> None:
    """按 profile 设置请求级生成能力覆盖，IMAGE/VIDEO 运行期全链路跟随角色配置。

    值为 None（跟随全局）的类别不进 dict；两类都跟随时置 None 等价无覆盖。
    """
    override: dict[str, str] = {}
    if profile.image_block_key:
        override["IMAGE"] = profile.image_block_key
    if profile.video_block_key:
        override["VIDEO"] = profile.video_block_key
    CTX_GEN_BLOCK_OVERRIDE.set(override or None)


def effective_chat_supports_vision() -> bool:
    """运行期视觉判定的唯一入口：当前请求有 profile 用 profile（跟随角色 chat 块），
    否则回退全局激活块（chat_model_supports_vision）。

    media_read_patch / db_tools 等共享工具的运行期读取点必须走这里——直接读全局标记
    会把全局块的视觉能力泄漏给选了文本块的角色用户（触发多模态直传爆上限）。
    """
    profile = CTX_PROFILE.get()
    if profile is not None:
        return profile.supports_vision
    return chat_model_supports_vision()


def clear_agent_instances() -> None:
    """角色模型配置写入后清按用户缓存的 agent 实例（failure-safe）。

    与 clear_model_caches 的 ③ 相同，但独立存在：角色配置变更只影响 agent 实例
    （块定义 / llm 单例没变），不必清全部 4 层。
    """
    try:
        from app.api.v1.ai.qa import _agent_cache

        _agent_cache.clear()
    except Exception:
        logger.exception("[role_model_profile] 清 _agent_cache 失败")


def _sort_role_objs(role_objs: list[Any]) -> list[Any]:
    """固定顺序排序：R_SUPER → R_ADMIN → R_USER → 自创角色（role.id 升序）。"""

    def _key(r: Any):
        return (_ROLE_ORDER.get(r.role_code, _CUSTOM_TIER), r.id or 0)

    return sorted(role_objs or [], key=_key)


async def resolve_profile_for_roles(role_objs: list[Any]) -> RoleModelProfile:
    """按角色对象列表解析 profile（整行生效规则；调用方通常已 prefetch 角色）。

    解析结果不自动 set CTX_PROFILE——qa.py 等入口自行 set_current_profile +
    apply_gen_override（保持入口对上下文的显式控制）。
    """
    from app.langchain.model_selection import _block_configured
    from app.models.standard import AgentRoleModelConfig

    rows = {r.role_code: r for r in await AgentRoleModelConfig.all()}

    row = None
    for r in _sort_role_objs(role_objs):
        if r.role_code in rows:
            row = rows[r.role_code]
            break
    if row is None:
        row = rows.get(OTHER_ROLE_CODE)

    chat: Optional[str] = None
    image: Optional[str] = None
    video: Optional[str] = None
    if row is not None:
        # chat：块名有效才用，失效落全局（chat 无 DISABLED 语义）
        if row.chat_block_key:
            if _block_configured("chat", row.chat_block_key):
                chat = row.chat_block_key
            else:
                logger.warning(f"[role_model_profile] 角色 {row.role_code} 的 chat 块 {row.chat_block_key} 不可用，落全局")
        # image / video：DISABLED 直通；块名失效落全局
        for cat, val in (("image", row.image_block_key), ("video", row.video_block_key)):
            picked: Optional[str] = None
            if val:
                if val.upper() == GEN_DISABLED:
                    picked = GEN_DISABLED
                elif _block_configured(cat, val):
                    picked = val
                else:
                    logger.warning(f"[role_model_profile] 角色 {row.role_code} 的 {cat} 块 {val} 不可用，落全局")
            if cat == "image":
                image = picked
            else:
                video = picked

    # 视觉能力跟随生效的 chat 块：指定块读块级字段；跟随全局走全局重定向判定
    supports_vision = chat_block_supports_vision(chat) if chat else chat_model_supports_vision()

    return RoleModelProfile(
        chat_block_key=chat,
        image_block_key=image,
        video_block_key=video,
        supports_vision=supports_vision,
    )


async def resolve_user_model_profile(user_id: Optional[int]) -> RoleModelProfile:
    """解析用户的模型配置 profile（容忍匿名：无角色 → OTHER 兜底 → 全局）。

    同请求记忆化：已解析过直接返回；否则查角色 + 配置表并 set CTX_PROFILE。
    调用方仍需自行 apply_gen_override（CTX 覆盖与记忆化解耦，入口显式控制）。
    """
    cached = CTX_PROFILE.get()
    if cached is not None:
        return cached

    role_objs: list[Any] = []
    if user_id is not None:
        from app.models.system.admin import User

        u = await User.get_or_none(id=user_id).prefetch_related("by_user_roles")
        if u:
            role_objs = list(u.by_user_roles)

    profile = await resolve_profile_for_roles(role_objs)
    CTX_PROFILE.set(profile)
    return profile
