"""
LangChain 配置模块

配置分两类，对应两种模型形态：

1. 对话/向量「角色块」（CHAT / VISION / EMBED / LOCAL_EMBED …）——都是 OpenAI 兼容接口，
   协议统一。每个角色独立一组 {ROLE}_BASE_URL / {ROLE}_API_KEY / {ROLE}_MODEL 字段，
   由 load_role(role) 读成 RoleConfig，交给 _build_chat / _build_embed 构造。
   新增角色三步走：
     1. .env 加一组 {ROLE}_* 字段
     2. config.py 不用动（load_role 通用读取）
     3. 在 llm_providers.py / embedding_providers.py 加一行工厂函数：
           def get_xxx_llm():     return _build_chat(load_role("XXX"))
           def get_xxx_embed():   return _build_embed(load_role("XXX"))

2. 生成类「能力块」（IMAGE / VIDEO，未来 TTS / MUSIC …）——对接异构异步 API，
   各家协议不同，无法套 _build_chat。每个能力独立一组四件套：
       {CAP}_PROVIDER   选哪个实现（qwen / gpt / ark / openrouter / happyhorse …）
       {CAP}_API_KEY    该能力的凭据（按能力独立，互不复用）
       {CAP}_MODEL      可选，留空走 client 内置默认
       {CAP}_BASE_URL   可选，留空走 client 内置默认
   由 load_provider(capability) 读成 ProviderConfig，各 client 按 provider 实现协议。
   新增能力：写一个 client + 在对应 tools 里加 get_xxx_tools()，.env 抄一组四件套即可。
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.ctx import CTX_GEN_BLOCK_OVERRIDE

# 让 load_role 通过 os.getenv 直接读到 .env 中的字段（pydantic-settings 不会写回 os.environ）
load_dotenv()

# 按角色模型配置的禁用哨兵（role 表里 image/video 块字段的三态之一）
GEN_DISABLED = "DISABLED"


class ConfigError(RuntimeError):
    """配置缺失或格式错误。"""


@dataclass(frozen=True)
class RoleConfig:
    """单个角色（CHAT / VISION / EMBED 等）的完整配置。"""

    role: str
    base_url: str
    api_key: str
    model: str

    # Chat-like 角色用
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None  # 模型上下文窗口大小（token），供 deepagents 计算压缩阈值
    thinking: Optional[bool] = None
    vision_supported: Optional[bool] = None  # 模型是否原生支持图片输入（只认块级 {BLOCK}_VISION_SUPPORTED，无回退）
    temperature: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    headers: Optional[str] = None  # JSON 字符串

    # Embedding 角色用
    dimension: Optional[int] = None
    batch_size: Optional[int] = None


@dataclass(frozen=True)
class ProviderConfig:
    """生成类能力（IMAGE / VIDEO / ...）的运行时配置。

    与 RoleConfig 的区别：这些能力对接异构异步 API（非 OpenAI 兼容 chat），
    各家协议不同，因此不复用 _build_chat，而是由各 client 按 provider 自行实现，
    本配置只统一「凭据 + 模型 + 端点」的读取方式。
    """

    capability: str  # IMAGE / VIDEO / ...（大写）
    provider: str  # qwen / gpt / ark / openrouter / happyhorse / ...（小写）
    api_key: str
    model: Optional[str] = None  # 留空 → client 用内置默认
    base_url: Optional[str] = None  # 留空 → client 用内置默认


def _env(key: str) -> Optional[str]:
    v = os.getenv(key)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _env_int(key: str) -> Optional[int]:
    v = _env(key)
    return int(v) if v is not None else None


def _env_float(key: str) -> Optional[float]:
    v = _env(key)
    return float(v) if v is not None else None


def _env_bool(key: str) -> Optional[bool]:
    v = _env(key)
    if v is None:
        return None
    return v.lower() in ("1", "true", "yes", "on")


# ── 激活块重定向（供"模型切换"页面用）───────────────────────────────────────
# 内存映射：category（chat/image/video）→ 当前选中的 .env 配置块名。
# load_role("CHAT") / load_provider("IMAGE"/"VIDEO") 据此把请求重定向到超管选中的块；
# 其余角色/能力不受影响（EMBED/VISION/LOCAL_EMBED 等仍读自己的块）。
# 写操作发生在启动加载与超管 PUT 之后（async 上下文），单 worker 下 dict 赋值即原子、无竞态。
_active_blocks: dict[str, str] = {}


def get_active_block(category: str, default: str) -> str:
    """返回某类别当前选中的配置块名；未设置时回退 default（即 .env 默认块）。"""
    return _active_blocks.get(category.lower(), default)


def set_active_block(category: str, block: str) -> None:
    """设置某类别选中的配置块名（统一大写存储）。"""
    _active_blocks[category.lower()] = block.upper()


@lru_cache(maxsize=None)
def load_role(role: str) -> RoleConfig:
    """
    从 .env 读取 {ROLE}_* 字段构造 RoleConfig。

    必填：{ROLE}_BASE_URL / {ROLE}_API_KEY / {ROLE}_MODEL
    缺失任一必填字段抛 ConfigError。
    """
    R = role.upper()
    # 激活块重定向：超管在页面切换主模型后，"CHAT" 指向选中的预设块（默认 CHAT_DASHSCOPE）
    if R == "CHAT":
        R = get_active_block("chat", "CHAT_DASHSCOPE")
    base_url = _env(f"{R}_BASE_URL")
    api_key = _env(f"{R}_API_KEY")
    model = _env(f"{R}_MODEL")

    missing = [
        k
        for k, v in [
            (f"{R}_BASE_URL", base_url),
            (f"{R}_API_KEY", api_key),
            (f"{R}_MODEL", model),
        ]
        if v is None
    ]
    if missing:
        raise ConfigError(f"角色 {R} 配置不完整，缺少: {', '.join(missing)}")

    # CHAT 系块通用配置：CHAT_CONTEXT_WINDOW / CHAT_THINKING 是对所有 CHAT_* 预设块
    # 生效的通用项；预设块未显式配置 {BLOCK}_CONTEXT_WINDOW / {BLOCK}_THINKING 时
    # 回退到它们（块级显式配置优先）。保证超管热切换模型后，自动压缩阈值与
    # thinking 开关依然生效，不会因预设块缺字段而退化。
    # 注意：vision_supported 不做通用回退——视觉能力是模型属性，只认块级
    # {BLOCK}_VISION_SUPPORTED，未配置按「不支持」处理（每个模型块各自声明）。
    context_window = _env_int(f"{R}_CONTEXT_WINDOW")
    thinking = _env_bool(f"{R}_THINKING")
    vision_supported = _env_bool(f"{R}_VISION_SUPPORTED")
    if R.startswith("CHAT_"):
        if context_window is None:
            context_window = _env_int("CHAT_CONTEXT_WINDOW")
        if thinking is None:
            thinking = _env_bool("CHAT_THINKING")

    return RoleConfig(
        role=R,
        base_url=base_url,  # type: ignore[arg-type]
        api_key=api_key,  # type: ignore[arg-type]
        model=model,  # type: ignore[arg-type]
        max_tokens=_env_int(f"{R}_MAX_TOKENS"),
        context_window=context_window,
        thinking=thinking,
        vision_supported=vision_supported,
        temperature=_env_float(f"{R}_TEMPERATURE"),
        frequency_penalty=_env_float(f"{R}_FREQUENCY_PENALTY"),
        presence_penalty=_env_float(f"{R}_PRESENCE_PENALTY"),
        headers=_env(f"{R}_HEADERS"),
        dimension=_env_int(f"{R}_DIMENSION"),
        batch_size=_env_int(f"{R}_BATCH_SIZE"),
    )


def has_role(role: str) -> bool:
    """检查角色是否已配置（{ROLE}_BASE_URL 存在即视为配置）。"""
    return _env(f"{role.upper()}_BASE_URL") is not None


def load_provider(capability: str) -> ProviderConfig:
    """
    从 .env 读取生成类能力配置（四件套）：

        {CAP}_PROVIDER / {CAP}_API_KEY  必填
        {CAP}_MODEL / {CAP}_BASE_URL    可选，留空走 client 内置默认

    缺失任一必填字段抛 ConfigError。provider 统一小写，调用方直接比较。

    请求级覆盖（按角色模型配置）：业务入口解析用户角色 profile 后经
    CTX_GEN_BLOCK_OVERRIDE 设置 {"IMAGE"/"VIDEO": 块名或 "DISABLED"}。
    命中 "DISABLED" → 抛 ConfigError（能力已被角色配置禁用）；命中块名 →
    按该块读 env（跳过激活块重定向）。6 个 client 与工具层全部经本函数读凭据，
    零调用点改动即跟随角色配置。

    缓存注意：结果依赖请求级 ContextVar，本函数自身不缓存；覆盖分发后委托给
    按「解析后块名」缓存的两个纯函数（_load_provider_default / _load_provider_block），
    避免不同 override 状态之间交叉污染（由 clear_model_caches 统一清）。
    """
    C = capability.upper()
    override = (CTX_GEN_BLOCK_OVERRIDE.get() or {}).get(C)
    if override:
        if override.upper() == GEN_DISABLED:
            raise ConfigError(f"能力 {C} 已被按角色模型配置禁用")
        return _load_provider_block(override.upper())
    return _load_provider_default(C)


@lru_cache(maxsize=None)
def _load_provider_default(C: str) -> ProviderConfig:
    """全局路径（可缓存）：IMAGE/VIDEO 先走激活块重定向，再按解析后的块名读 env。"""
    # 激活块重定向：超管在页面切换生图/生视频模型后，"IMAGE"/"VIDEO" 指向选中的预设块
    if C in ("IMAGE", "VIDEO"):
        C = get_active_block(C.lower(), C)
    return _read_provider_env(C)


@lru_cache(maxsize=None)
def _load_provider_block(block_key: str) -> ProviderConfig:
    """角色指定块路径（可缓存，按块名）：不走激活块重定向，直接按块读 env。"""
    return _read_provider_env(block_key)


def _read_provider_env(C: str) -> ProviderConfig:
    """按块/能力名读 env 四件套（纯函数，无缓存、无重定向）。"""
    provider = _env(f"{C}_PROVIDER")
    api_key = _env(f"{C}_API_KEY")

    missing = [
        k
        for k, v in [
            (f"{C}_PROVIDER", provider),
            (f"{C}_API_KEY", api_key),
        ]
        if v is None
    ]
    if missing:
        raise ConfigError(f"能力 {C} 配置不完整，缺少: {', '.join(missing)}")

    return ProviderConfig(
        capability=C,
        provider=provider.lower(),  # type: ignore[union-attr]
        api_key=api_key,  # type: ignore[arg-type]
        model=_env(f"{C}_MODEL"),
        base_url=_env(f"{C}_BASE_URL"),
    )


def has_capability(capability: str) -> bool:
    """能力是否已启用：{CAP}_PROVIDER 与 {CAP}_API_KEY 均配置。

    请求级覆盖（按角色模型配置）：override 命中 "DISABLED" → False（角色禁用了该
    能力，get_image_tools/get_video_tools 构建期返回 []）；命中块名 → 查该块的 env。
    """
    C = capability.upper()
    override = (CTX_GEN_BLOCK_OVERRIDE.get() or {}).get(C)
    if override:
        if override.upper() == GEN_DISABLED:
            return False
        C = override.upper()
    return _env(f"{C}_PROVIDER") is not None and _env(f"{C}_API_KEY") is not None


def chat_model_supports_vision() -> bool:
    """当前激活 CHAT 模型是否原生支持视觉输入（与模型块绑定）。

    只读激活块自己的 `{BLOCK}_VISION_SUPPORTED`（如 `CHAT_GROK_VISION_SUPPORTED`、
    `CHAT_DASHSCOPE_VISION_SUPPORTED`），不做任何回退与模型名猜测；未配置按「不支持」处理。
    - 支持图片输入的模型块（如 qwen3.8-max）→ 设 true，图片多模态直传给主模型
    - 纯文本模型块 → 设 false / 不配，图片走 vision_inspect → VISION 角色兜底
    - 新增模型块时按需配 {BLOCK}_VISION_SUPPORTED，切换主模型即自动跟随，无需改代码
    """
    try:
        return load_role("CHAT").vision_supported is True
    except ConfigError:
        return False


def chat_block_supports_vision(block_key: str) -> bool:
    """指定 chat 预设块是否原生支持视觉输入（按角色模型配置用）。

    与 chat_model_supports_vision 同规则，只是不走激活块重定向——直接读指定块自己的
    `{BLOCK}_VISION_SUPPORTED`，无回退、无模型名猜测；块配置不全 → False。
    """
    try:
        return load_role(block_key).vision_supported is True
    except ConfigError:
        return False


class LangChainConfig(BaseSettings):
    """
    全局通用参数（与具体角色无关）。

    模型相关字段不再放这里，去 RoleConfig / load_role(role) 拿。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 默认采样温度，单角色未显式配置 {ROLE}_TEMPERATURE 时使用
    DEFAULT_TEMPERATURE: float = Field(default=0.6, ge=0.0, le=1.0)
    # 通用并发上限（业务侧 LLM 批量调用用）
    LLM_MAX_CONCURRENT: int = Field(default=20, ge=1)
    # 通用 HTTP 超时（非 LLM 用）
    REQUEST_TIMEOUT: int = Field(default=60)
    # LLM 请求超时（秒），长链路 agent 默认 300
    LLM_REQUEST_TIMEOUT: int = Field(default=300)
    # Embedding 并发上限（DashScope 60 QPM 限流时建议 ≤ 8）
    EMBEDDING_MAX_CONCURRENT: int = Field(default=8, ge=1)

    # DashScope 平台 API Key，仅供平台级服务（OSS 上传、WebSearch MCP）使用。
    # 图像/视频生成不复用它——那些能力各自用 {CAP}_API_KEY（见 load_provider）。
    DASHSCOPE_API_KEY: str = Field(default="")

    # 积分汇率：1 积分对应多少元（首版 0.001 即 1 元 = 1000 积分）。
    # 改汇率 = 改 .env + 一次性 UPDATE agent_usage_log SET credits = cost_yuan / 新值；
    # 切勿仅改 .env 不动历史数据，否则趋势图会出现与实际不符的跳变。
    CREDIT_RATE_YUAN: float = Field(default=0.001, gt=0.0)

    # LangSmith 追踪（可选）
    LANGCHAIN_TRACING_V2: bool = Field(default=False)
    LANGCHAIN_API_KEY: str = Field(default="")
    LANGCHAIN_PROJECT: str = Field(default="cesi-fast-admin")

    # 公网访问基础 URL（用于生成图片等资源的公网可访问地址）
    # 示例：https://your-domain.com（不含尾部斜杠）
    PUBLIC_BASE_URL: str = Field(default="")


langchain_config = LangChainConfig()
