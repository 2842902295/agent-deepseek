"""deepagents SkillsMiddleware 活载补丁：每轮重载技能清单（破除 checkpoint 冻结）。

背景：
- SkillsMiddleware.before_agent/abefore_agent 的逻辑是「state 里已有 skills_metadata
  就跳过扫描、直接返回 None」。state 来自 checkpointer → 会话首轮加载后技能清单
  永久冻结：用户在面板里启停技能导致的 workspace .agent_skills/ 目录变化，
  在存量会话里永远体现不出来（技能提示词不增不减）。
- create_deep_agent 内部为主 agent 与各子 agent 硬编码构造该 middleware
  （deepagents/graph.py），无法注入子类，只能类级 monkeypatch（与
  media_read_patch 同一范式）。

做法：幂等包装 before_agent / abefore_agent ——
1. 调原方法前把 state 浅拷贝并剔除 skills_metadata / skills_load_errors 两个 key，
   强制原方法走「重新扫描 .agent_skills/」分支（原方法见 key 即返回 None）；
2. 返回的 update 里 skills_metadata 按 name 排序 —— Path.iterdir() 顺序不稳定，
   顺序漂移会破坏 system prompt 的前缀缓存；
3. 恒写回 skills_load_errors=[]（LastValue 通道直接覆盖），清掉 checkpoint 里
   残留的历史告警。

fail-safe：导入失败 / 原方法缺失 / 包装异常一律只 log 不抛——deepagents 升级
时宁可退回「冻结」旧行为，也不拖垮启动。
"""

from loguru import logger

_PATCHED_FLAG = "_cesi_skills_live_reload_patched"


def _fresh_state(state):
    """浅拷贝 state 并剔除冻结检查 key，强制原方法走重扫分支。"""
    try:
        fresh = dict(state)
        fresh.pop("skills_metadata", None)
        fresh.pop("skills_load_errors", None)
        return fresh
    except Exception:
        return state


def _normalize_update(update):
    """skills_metadata 按 name 排序（稳 prompt 前缀缓存）+ 恒写回 skills_load_errors。"""
    if not isinstance(update, dict):
        return update
    skills = update.get("skills_metadata")
    if isinstance(skills, list):
        try:
            update["skills_metadata"] = sorted(
                skills,
                key=lambda s: str(s.get("name", "")) if isinstance(s, dict) else "",
            )
        except Exception:
            pass
    update.setdefault("skills_load_errors", [])
    return update


def patch_skills_live_reload() -> None:
    """幂等应用补丁：SkillsMiddleware 每次 agent 调用都重扫技能目录。"""
    try:
        from deepagents.middleware.skills import SkillsMiddleware
    except Exception as e:  # pragma: no cover - deepagents 缺失时不拖垮启动
        logger.warning(f"[skills_live_reload] SkillsMiddleware 导入失败，跳过补丁: {e}")
        return

    if getattr(SkillsMiddleware, _PATCHED_FLAG, False):
        return

    orig_sync = getattr(SkillsMiddleware, "before_agent", None)
    orig_async = getattr(SkillsMiddleware, "abefore_agent", None)
    if orig_sync is None or orig_async is None:
        logger.warning("[skills_live_reload] 未找到原 before_agent/abefore_agent，跳过补丁")
        return

    def before_agent_live(self, state, runtime, config):
        update = orig_sync(self, _fresh_state(state), runtime, config)
        return _normalize_update(update)

    async def abefore_agent_live(self, state, runtime, config):
        update = await orig_async(self, _fresh_state(state), runtime, config)
        return _normalize_update(update)

    try:
        SkillsMiddleware.before_agent = before_agent_live
        SkillsMiddleware.abefore_agent = abefore_agent_live
        setattr(SkillsMiddleware, _PATCHED_FLAG, True)
        logger.info("[skills_live_reload] SkillsMiddleware 每轮活载补丁已应用")
    except Exception as e:  # pragma: no cover
        logger.warning(f"[skills_live_reload] 补丁应用失败，技能清单维持冻结旧行为: {e}")
