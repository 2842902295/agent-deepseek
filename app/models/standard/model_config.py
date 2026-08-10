"""
模型配置 ORM（超管全局切换 + 按角色配置）。

三张表分工：
- AgentModelBlock  预设块定义（运行时真相源）：chat / image / video 三类预设块的
  完整定义（端点、凭据、模型名、显示名等）。播种基线在代码
  model_selection.py::_SEED_PRESETS，新装库首启幂等插入本表（表里有同名块则跳过，
  软删除的块不复活）；.env 不存放任何预设块。
  运行期一律以本表为准，load_role/load_provider 经「env 物化」消费本表数据。
  增删改由 system-admin 子 agent 的 admin_save_record/admin_delete_record 完成，
  写后自动重载生效（model_selection.reload_model_blocks）。
- AgentModelConfig 每类别当前选中的块名（selected_key），仅 3 行，原地 UPDATE，不删除。
- AgentRoleModelConfig 按角色的模型配置（每角色一行）：块字段三态
  null=跟随全局 / "DISABLED"=禁用（仅 image/video）/ 块名。
  role_code=OTHER 为保留兜底行（roles 表中不存在，禁建同名角色）。
  解析规则见 role_model_profile.py：固定顺序首个命中整行生效 → OTHER → 全局。
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentModelBlock(BaseModel, TimestampMixin):
    """预设模型块定义（chat / image / video 三类统一管理）。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    block_key = fields.CharField(max_length=64, unique=True, description="块名（唯一），如 CHAT_DASHSCOPE / VIDEO_APIPOD")
    category = fields.CharField(max_length=16, description="类别：chat / image / video")
    label = fields.CharField(max_length=128, description="页面显示名，如 百炼 · Qwen3.8-Max")
    provider = fields.CharField(max_length=32, null=True, default=None, description="生图：gpt/apipod/qwen；生视频：ark/openrouter/apipod/happyhorse；chat 留空（统一 OpenAI 兼容协议）")
    base_url = fields.CharField(max_length=256, null=True, default=None, description="服务端点（chat 必填；image/video 可空走 client 内置默认）")
    api_key = fields.CharField(max_length=256, null=True, default=None, description="凭据（必填）；管理界面与审计日志一律脱敏回显")
    model = fields.CharField(max_length=128, null=True, default=None, description="模型名（chat 必填；image/video 可空走 client 内置默认）")
    vision_supported = fields.BooleanField(null=True, default=None, description="chat 专用：模型是否原生支持图片输入（只认本字段，未配置按不支持处理）")
    thinking = fields.BooleanField(null=True, default=None, description="chat 专用：思考模式开关（未配置回退 CHAT_THINKING 通用值）")
    context_window = fields.IntField(null=True, default=None, description="chat 专用：上下文窗口（未配置回退 CHAT_CONTEXT_WINDOW 通用值）")
    sort_order = fields.IntField(default=0, description="同类别页面展示顺序，小的在前")
    is_deleted = fields.IntField(default=0, description="软删除：0=正常 1=已删除")

    class Meta:
        table = "agent_model_block"
        table_description = "模型预设块定义（超管全局切换的清单与凭据）"


class AgentModelConfig(BaseModel, TimestampMixin):
    """每个类别当前激活的模型预设块。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    category = fields.CharField(max_length=16, unique=True, description="类别：chat / image / video")
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
