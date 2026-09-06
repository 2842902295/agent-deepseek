"""
模型配置 ORM（超管全局切换 + 按角色配置）。

三张表分工：
- AgentModelBlock  预设块定义（运行时真相源）：chat / image / video / embed 四类预设块的
  完整定义（端点、凭据、模型名、显示名等）。播种基线在代码
  model_selection.py::_SEED_PRESETS，新装库首启幂等插入本表（表里有同名块则跳过，
  软删除的块不复活）；.env 不存放任何预设块。
  运行期一律以本表为准，load_role/load_provider 经「env 物化」消费本表数据。
  增删改由 system-admin 子 agent 的 admin_save_record/admin_delete_record 完成，
  写后自动重载生效（model_selection.reload_model_blocks）。
- AgentModelConfig 每类别当前选中的块名（selected_key），仅 4 行，原地 UPDATE，不删除。
- AgentRoleModelConfig 按角色的模型配置（每角色一行）：块字段三态
  null=跟随全局 / "DISABLED"=禁用（仅 image/video）/ 块名。
  role_code=OTHER 为保留兜底行（roles 表中不存在，禁建同名角色）。
  解析规则见 role_model_profile.py：固定顺序首个命中整行生效 → OTHER → 全局。
- AgentChatModeConfig 对话模式配置（超管配置，每模式一行，可增删改）：
  mode → chat 预设块（null=兜底 角色配置 → 全局激活块）+ label/note/sort_order。
  fast/balanced/complex 三行为启动幂等播种；balanced 为系统兜底行（不可删除）。
- AgentUserChatPref 每用户对话偏好（一人一行）：所选模式 + 思考强度
  （wire 档位值 none/low/medium/high/max，按模型块 reasoning_levels 白名单校验，
  存量 legacy token standard/advanced/ultimate 读时翻译）。
  解析与默认值见 app/langchain/chat_mode.py。
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentModelBlock(BaseModel, TimestampMixin):
    """预设模型块定义（chat / image / video / embed 四类统一管理）。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    block_key = fields.CharField(max_length=64, unique=True, description="块名（唯一），如 CHAT_DASHSCOPE / VIDEO_APIPOD / EMBED_DASHSCOPE")
    category = fields.CharField(max_length=16, description="类别：chat / image / video / embed")
    label = fields.CharField(max_length=128, description="页面显示名，如 百炼 · Qwen3.8-Max")
    provider = fields.CharField(max_length=32, null=True, default=None, description="生图：gpt/apipod/qwen；生视频：ark/openrouter/apipod/happyhorse/wan；chat 留空（统一 OpenAI 兼容协议）")
    base_url = fields.CharField(max_length=256, null=True, default=None, description="服务端点（chat 必填；image/video 可空走 client 内置默认）")
    api_key = fields.CharField(max_length=256, null=True, default=None, description="凭据（必填）；管理界面与审计日志一律脱敏回显")
    model = fields.CharField(max_length=128, null=True, default=None, description="模型名（chat 必填；image/video 可空走 client 内置默认）")
    vision_supported = fields.BooleanField(null=True, default=None, description="chat 专用：模型是否原生支持图片输入（只认本字段，未配置按不支持处理）")
    thinking = fields.BooleanField(null=True, default=None, description="chat 专用：思考模式开关（未配置回退 CHAT_THINKING 通用值）")
    context_window = fields.IntField(null=True, default=None, description="chat 专用：上下文窗口（未配置回退 CHAT_CONTEXT_WINDOW 通用值）")
    reasoning_levels = fields.CharField(
        max_length=128, null=True, default=None,
        description="chat 专用：思考强度档位白名单（逗号分隔 wire 值 none/minimal/low/medium/high/max，顺序即滑块序）；空/NULL=不展示强度滑块",
    )
    reasoning_default = fields.CharField(max_length=16, null=True, default=None, description="chat 专用：思考强度默认档（须 ∈ reasoning_levels）；空=默认不开（levels 含 none 取 none，否则最低档）")
    dimension = fields.IntField(null=True, default=None, description="embed 专用：向量输出维度（向量库维度一致性检查与建表用；切换不同维度的块会使既有向量库陈旧）")
    sort_order = fields.IntField(default=0, description="同类别页面展示顺序，小的在前")
    is_deleted = fields.IntField(default=0, description="软删除：0=正常 1=已删除")

    class Meta:
        table = "agent_model_block"
        table_description = "模型预设块定义（超管全局切换的清单与凭据）"


class AgentModelConfig(BaseModel, TimestampMixin):
    """每个类别当前激活的模型预设块。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    category = fields.CharField(max_length=16, unique=True, description="类别：chat / image / video / embed")
    selected_key = fields.CharField(max_length=64, description="选中的预设块名（对应 agent_model_block.block_key）")
    updated_by = fields.BigIntField(null=True, description="最后修改人用户ID")

    class Meta:
        table = "agent_model_config"
        table_description = "模型选择配置（超管全局切换）"


class AgentRoleModelConfig(BaseModel, TimestampMixin):
    """按角色的模型配置（超管配置，每角色一行）。

    块字段三态：null=跟随全局；"DISABLED"=禁用（仅 image/video）；块名=指定预设块。
    解析：用户角色按 R_SUPER>R_ADMIN>R_USER>自创（role.id 升序）固定顺序找，
    第一个有行的角色整行生效（行内 null 字段跟随全局）→ OTHER 兜底行 → 全局。
    """

    id = fields.BigIntField(pk=True, description="主键ID")
    role_code = fields.CharField(max_length=20, unique=True, description="角色编码（对应 roles.role_code）；OTHER 为保留兜底行，不属于任何真实角色")
    chat_block_key = fields.CharField(max_length=64, null=True, default=None, description="对话模型块名（category=chat）；null=跟随全局")
    image_block_key = fields.CharField(max_length=64, null=True, default=None, description="生图块名；null=跟随全局；DISABLED=禁用")
    video_block_key = fields.CharField(max_length=64, null=True, default=None, description="生视频块名；null=跟随全局；DISABLED=禁用")
    updated_by = fields.BigIntField(null=True, description="最后修改人用户ID")

    class Meta:
        table = "agent_role_model_config"
        table_description = "按角色的模型配置（chat 块选择 + 生图/生视频块选择或禁用）"


class AgentChatModeConfig(BaseModel, TimestampMixin):
    """对话模式配置（超管配置，每模式一行，可增删改；本表 = 模式清单真相源）。

    mode → chat 预设块映射；chat_block_key 为 null 时该模式走兜底链
    （角色模型配置 → 全局激活块）。用户在输入框处切换模式，见 chat_mode.py。
    fast/balanced/complex 三行由 ensure_seed 启动幂等播种（label/note/sort_order
    空则回填）；balanced 为系统兜底行，删除接口拒绝、启动时缺失即重建。
    """

    id = fields.BigIntField(pk=True, description="主键ID")
    mode = fields.CharField(max_length=16, unique=True, description="模式 key（唯一，建后不可改）：播种 fast / balanced / complex；自定义 ^[a-z][a-z0-9_-]{1,15}$")
    label = fields.CharField(max_length=32, null=True, default=None, description="模式显示名（如 快速/均衡/复杂）；空则前端回退 key")
    note = fields.CharField(max_length=128, null=True, default=None, description="composer 弹层小字说明（一句话描述该模式适用场景）")
    sort_order = fields.IntField(default=0, description="展示顺序，小的在前")
    chat_block_key = fields.CharField(max_length=64, null=True, default=None, description="chat 预设块名；null=兜底（角色配置→全局）")
    updated_by = fields.BigIntField(null=True, description="最后修改人用户ID")

    class Meta:
        table = "agent_chat_mode_config"
        table_description = "对话模式配置（模式清单 → chat 预设块，可增删改）"


class AgentUserChatPref(BaseModel, TimestampMixin):
    """每用户对话偏好（一人一行；无行 = 默认 balanced + 块默认档（通常关闭））。

    thinking_level 存 wire 档位值（none/low/medium/high/max），读时按当前有效块的
    reasoning_levels 校验：合法原样；不合法（含存量 legacy token
    standard/advanced/ultimate，先经 LEGACY_LEVEL_MAP 翻译）按序数就近平滑迁移到
    相邻档位。解析见 chat_mode.resolve_user_chat_pref。
    """

    id = fields.BigIntField(pk=True, description="主键ID")
    user_id = fields.IntField(unique=True, description="所属用户ID")
    mode = fields.CharField(max_length=16, null=True, default=None, description="所选模式 key；null=默认；指向被删模式时读时回落 balanced")
    thinking_level = fields.CharField(max_length=16, null=True, default=None, description="思考强度 wire 档位值；null=块默认档（默认不开）")

    class Meta:
        table = "agent_user_chat_pref"
        table_description = "用户对话偏好（模式 + 思考强度）"
