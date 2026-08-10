from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentSession(BaseModel, TimestampMixin):
    """智能体会话"""

    id = fields.IntField(pk=True, description="主键ID")

    session_key = fields.CharField(max_length=64, unique=True, description="会话 key，前后端统一标识")
    user_id = fields.IntField(null=True, description="所属用户ID（跨库不做外键）")
    workflow_key = fields.CharField(max_length=64, null=True, description="关联工作流 key（画板内发起的会话）；null=通用问答")

    title = fields.CharField(max_length=128, default="新对话", description="会话标题")
    thread_id = fields.CharField(max_length=96, description="LangGraph thread id")

    message_count = fields.IntField(default=0, description="消息数量（冗余）")
    is_deleted = fields.IntField(default=0, description="软删：0 未删 1 已删")
    is_starred = fields.IntField(default=0, description="收藏：0 否 1 是")
    source = fields.CharField(max_length=16, default="qa", description="会话来源：qa=普通问答 workflow=工作流画板")
    branch_from_thread_id = fields.CharField(max_length=96, null=True, description="分叉来源 thread_id（branch 时记录，首次对话时预注入历史）")

    class Meta:
        table = "agent_session"
        table_description = "智能体会话"
        indexes = [
            ("user_id", "is_deleted", "update_time"),
            ("session_key",),
        ]


class AgentMessage(BaseModel, TimestampMixin):
    """智能体消息"""

    id = fields.BigIntField(pk=True, description="主键ID")

    session_id = fields.IntField(description="所属会话ID（agent_session.id）")
    role = fields.CharField(max_length=16, description="user/assistant/batch")

    content = fields.TextField(null=True, description="最终答复或用户输入")
    thinking = fields.TextField(null=True, description="思考过程")
    tool_steps_json = fields.JSONField(null=True, description="工具调用轨迹")
    attachments_json = fields.JSONField(null=True, description="用户消息附件列表 [{name,path,size,isImage}]")

    status = fields.CharField(max_length=16, default="done", description="streaming/done/error/aborted")
    error = fields.TextField(null=True, description="错误信息")

    class Meta:
        table = "agent_message"
        table_description = "智能体消息"
        indexes = [
            ("session_id", "id"),
        ]


class AgentSkill(BaseModel, TimestampMixin):
    """Agent 能力（capability）：用户 @ 召唤的单元，可能调度一个或多个 skill 包"""

    id = fields.IntField(pk=True, description="主键ID")

    skill_key = fields.CharField(max_length=64, unique=True, description="能力 key，用于 @ 匹配")
    name = fields.CharField(max_length=64, description="显示名")
    description = fields.CharField(max_length=1000, null=True, description="简短描述")

    skill_md = fields.TextField(description="SKILL.md 主文件全文（含 YAML frontmatter），命中时注入给 agent")
    skill_pkg_keys = fields.JSONField(null=True, description="引用的其他技能 key 列表（来自 prompt 的 YAML frontmatter skills 字段，自动维护）")

    version = fields.CharField(max_length=32, null=True, description="当前版本号（有文件时填）")
    source_url = fields.CharField(max_length=512, null=True, description="安装来源 URL（discovered/uploaded 时）")

    source = fields.CharField(
        max_length=16,
        default="curated",
        description="builtin 内置 / official 官方（超管指定，key 不带用户名前缀）/ derived 凝练 / curated 收录（上传与发现合并）",
    )
    origin_session_id = fields.IntField(null=True, description="derived 时来源会话ID")

    user_id = fields.IntField(null=True, description="所属用户ID；null 表示公共能力")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")
    visibility = fields.CharField(
        max_length=16,
        default="private",
        description="可见性：private 仅创建者 / role 指定角色 / public 全员",
    )
    allowed_role_codes = fields.JSONField(
        null=True,
        description="visibility=role 时生效的角色 code 列表",
    )
    tags = fields.JSONField(
        null=True,
        description="用户自定义标签列表（字符串数组）",
    )

    class Meta:
        table = "agent_skill"
        table_description = "Agent 能力（capability）"
        indexes = [
            ("user_id", "is_enabled"),
            ("source",),
            ("skill_key",),
        ]


class AgentSkillFile(BaseModel, TimestampMixin):
    """技能文件：用户技能的所有文件存 DB（BLOB），按版本管理，运行时按需物化到磁盘"""

    id = fields.BigIntField(pk=True, description="主键ID")

    skill_key = fields.CharField(max_length=64, index=True, description="所属技能 key（对应 agent_skill.skill_key）")
    path = fields.CharField(max_length=512, description="文件相对路径，如 scripts/run.py、templates/tpl.md")
    content = fields.BinaryField(description="文件内容（文本存 UTF-8 bytes，二进制存原始 bytes）")
    size = fields.IntField(default=0, description="文件字节数")
    is_binary = fields.BooleanField(default=False, description="是否二进制文件（true 时 skill_read 不返回内容）")

    version = fields.CharField(max_length=32, default="1.0.0", description="所属版本号")
    is_active = fields.BooleanField(default=True, description="True=当前激活版本 False=历史版本")

    class Meta:
        table = "agent_skill_file"
        table_description = "技能文件（BLOB 存储 + 版本管理）"
        unique_together = [("skill_key", "path", "version")]
        indexes = [
            ("skill_key", "is_active"),
        ]


class AgentArtifact(BaseModel, TimestampMixin):
    """Agent 产物：可下载文件或可渲染的 chart"""

    id = fields.BigIntField(pk=True, description="主键ID")

    artifact_type = fields.CharField(
        max_length=16,
        description="md/pdf/zip/xlsx/csv/json/image/chart/excalidraw/other",
    )
    name = fields.CharField(max_length=200, description="展示名")
    description = fields.CharField(max_length=1000, null=True, description="简短说明")

    # 文件类：workspace 下的相对路径（相对项目根）
    path = fields.CharField(max_length=512, null=True, description="文件相对路径")
    size = fields.IntField(null=True, description="文件字节数")

    # chart 类：直接存 JSON spec
    chart_spec = fields.JSONField(null=True, description="chart JSON 规范")

    # 归属
    message_id = fields.BigIntField(null=True, description="归属 assistant 消息 ID")
    session_id = fields.IntField(null=True, description="所属会话 ID（冗余，加速按会话清理）")

    download_token = fields.CharField(max_length=64, null=True, description="下载 token（短时）")

    class Meta:
        table = "agent_artifact"
        table_description = "Agent 产物"
        indexes = [
            ("message_id",),
            ("session_id",),
        ]


class AgentDailyBrief(BaseModel, TimestampMixin):
    """每日简报：Agent 自动生成的每日回顾与推荐"""

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    brief_date = fields.DateField(description="简报日期")

    content_html = fields.TextField(null=True, description="生成的完整 HTML 内容")
    content_json = fields.JSONField(null=True, description="结构化数据（话题、推荐问题、统计等）")

    prev_brief_id = fields.BigIntField(null=True, description="上一条简报 ID（构成链表）")
    ref_session_keys = fields.JSONField(null=True, description="参考的会话 key 列表")
    topics_json = fields.JSONField(null=True, description="识别出的持续关注话题")

    generation_status = fields.CharField(max_length=16, default="done", description="generating/done/error")
    error = fields.TextField(null=True, description="生成失败时的错误信息")

    class Meta:
        table = "agent_daily_brief"
        table_description = "每日简报"
        unique_together = [("user_id", "brief_date")]
        indexes = [
            ("user_id", "brief_date"),
        ]


class AgentQuickAction(BaseModel, TimestampMixin):
    """快捷功能：用户可快速启动的预设场景（如流程图生成、智能问数等）"""

    id = fields.IntField(pk=True, description="主键ID")

    name = fields.CharField(max_length=64, description="功能名称，如：流程图生成")
    skill_key = fields.CharField(max_length=64, null=True, description="关联的技能 key（可为空）")
    icon = fields.CharField(max_length=128, null=True, description="图标标识（iconify 图标名，如 mdi:chart-bar）")
    description = fields.CharField(max_length=500, null=True, description="功能描述")

    sort_order = fields.IntField(default=0, description="全局排序（未分组区与快捷按钮列表用），越小越靠前")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")

    visibility = fields.CharField(
        max_length=16,
        default="public",
        description="可见性：private 仅管理员 / role 指定角色 / public 全员",
    )
    allowed_role_codes = fields.JSONField(
        null=True,
        description="visibility=role 时生效的角色 code 列表",
    )

    created_by = fields.IntField(null=True, description="创建者用户ID")

    class Meta:
        table = "agent_quick_action"
        table_description = "Agent 快捷功能"
        indexes = [
            ("is_enabled", "sort_order"),
            ("skill_key",),
        ]


class AgentQuickActionExample(BaseModel, TimestampMixin):
    """快捷功能案例：每个快捷功能可关联多个使用案例（历史会话）"""

    id = fields.IntField(pk=True, description="主键ID")

    action_id = fields.IntField(description="所属快捷功能ID（agent_quick_action.id）")
    title = fields.CharField(max_length=128, description="案例标题")
    description = fields.CharField(max_length=500, null=True, description="案例描述")

    # 案例数据（会话内容）
    conversation_data = fields.JSONField(description="会话数据 JSON：[{role, content, thinking?, attachments?}, ...]")
    preview_image = fields.CharField(max_length=512, null=True, description="预览图片路径（兼容旧数据，新数据存入 preview_images）")
    preview_images = fields.JSONField(null=True, description="预览图片路径列表（多张图片，JSON 数组）")
    preview_html = fields.TextField(null=True, description="预览 HTML 片段（可选）")

    # 来源信息
    source_session_id = fields.IntField(null=True, description="来源会话ID（从已有会话提取）")
    source_message_ids = fields.JSONField(null=True, description="来源消息ID列表（用于标记案例来自哪些消息）")

    sort_order = fields.IntField(default=0, description="排序，越小越靠前")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")

    created_by = fields.IntField(null=True, description="创建者用户ID")

    class Meta:
        table = "agent_quick_action_example"
        table_description = "Agent 快捷功能案例"
        indexes = [
            ("action_id", "is_enabled", "sort_order"),
            ("source_session_id",),
        ]


class AgentQuickActionCategory(BaseModel, TimestampMixin):
    """快捷功能展示类型：用户页橱窗的章节（写标准 / 用标准 / 日常办公 …）"""

    id = fields.IntField(pk=True, description="主键ID")

    name = fields.CharField(max_length=32, description="类型名，如：写标准")
    sort_order = fields.IntField(default=0, description="类型排序（章节顺序），越小越靠前")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")

    created_by = fields.IntField(null=True, description="创建者用户ID")

    class Meta:
        table = "agent_quick_action_category"
        table_description = "Agent 快捷功能类型"
        indexes = [
            ("sort_order",),
        ]


class AgentQuickActionLink(BaseModel):
    """快捷功能 ↔ 类型 的多对多关联（带序）：sort_order 是该功能在该类型内的排序"""

    id = fields.IntField(pk=True, description="主键ID")

    action_id = fields.IntField(description="快捷功能ID（agent_quick_action.id）")
    category_id = fields.IntField(description="类型ID（agent_quick_action_category.id）")
    sort_order = fields.IntField(default=0, description="类型内排序，越小越靠前")

    class Meta:
        table = "agent_quick_action_link"
        table_description = "Agent 快捷功能-类型关联"
        indexes = [
            ("category_id", "sort_order"),
            ("action_id",),
        ]


class AgentProfession(BaseModel, TimestampMixin):
    """用户职业：引导新用户选择身份，并按职业推荐一批快捷功能"""

    id = fields.IntField(pk=True, description="主键ID")

    name = fields.CharField(max_length=32, description="职业名，如：标准编制人员")
    icon = fields.CharField(max_length=128, null=True, description="图标标识（iconify 图标名）")
    description = fields.CharField(max_length=200, null=True, description="职业一句话描述")

    recommended_action_ids = fields.JSONField(null=True, description="该职业推荐的快捷功能 ID 数组")

    sort_order = fields.IntField(default=0, description="排序，越小越靠前")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")

    created_by = fields.IntField(null=True, description="创建者用户ID")

    class Meta:
        table = "agent_profession"
        table_description = "Agent 用户职业"
        indexes = [
            ("is_enabled", "sort_order"),
        ]


class AgentUserSubscription(BaseModel, TimestampMixin):
    """用户快捷功能订阅：记录每个用户最终勾选/收藏的功能（首屏橱窗与对话框优先展示）"""

    id = fields.IntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    action_id = fields.IntField(description="快捷功能ID（agent_quick_action.id）")

    sort_order = fields.IntField(default=0, description="用户侧排序，越小越靠前")

    class Meta:
        table = "agent_user_subscription"
        table_description = "Agent 用户快捷功能订阅"
        unique_together = [("user_id", "action_id")]
        indexes = [
            ("user_id", "sort_order"),
        ]


class AgentWorkflow(BaseModel, TimestampMixin):
    """Agent 共享工作流（人和 Agent 共读共写的 Vue Flow JSON）"""

    id = fields.BigIntField(pk=True, description="主键ID")

    workflow_key = fields.CharField(max_length=64, unique=True, description="工作流 key（wf_ + hex）")
    session_key = fields.CharField(max_length=64, null=True, description="创建来源会话（仅记录，不绑定）")
    user_id = fields.IntField(null=True, description="所属用户ID")

    title = fields.CharField(max_length=128, default="未命名工作流", description="工作流标题")
    # 板型：board=节点连线流程板（默认）；html=HTML 看板（agent 在 apps/{workflow_key}/ 目录开发多文件
    # HTML 应用，前端 iframe 画布渲染）。读取侧一律 `wf.board_type or "board"` 兜底防脏数据。
    board_type = fields.CharField(max_length=16, default="board", description="板型：board 流程板 / html HTML看板")
    nodes = fields.JSONField(null=True, description="Vue Flow 节点列表")
    edges = fields.JSONField(null=True, description="Vue Flow 连线列表")
    viewport = fields.JSONField(null=True, description="视口状态 {x, y, zoom}")
    version = fields.IntField(default=0, description="版本号，每次写入 +1")
    # 人机协作信号：human_edit 存「人最近一次改动的详尽简报」（前端程序算好：节点增删带标签+内容速览，
    # 编辑节点字段级「旧值」→「新值」，连线增减带两端标签，标题旧→新）；
    # editor 标记上一次写入者(human/agent)。agent 读板时若 editor=human 则带上 human_edit，自己改板时清空。
    human_edit = fields.JSONField(null=True, description="人最近一次改动详尽简报 {added,edited,removed,edgesAdded,edgesRemoved,title}")
    editor = fields.CharField(max_length=8, null=True, description="上一次写入者 human/agent")
    # 节点徽标（临时协作态）：{nodes: {节点id: {t: new/human/agent}}, edges: {连线id: {t: new}}}
    # new=agent 本轮新增；agent=agent 改过；human=人改过（人端写入）。纯前端视觉信号，不进 read_workflow。
    # 生命周期：agent 每次 edit_workflow_board 全量重建（旧徽标随之清空），避免堆积。
    marks = fields.JSONField(null=True, description="节点/连线徽标（临时协作态，agent 下次编辑全量重建）")

    is_deleted = fields.IntField(default=0, description="软删：0 未删 1 已删")

    class Meta:
        table = "agent_workflow"
        table_description = "Agent 共享工作流"
        indexes = [
            ("session_key",),
            ("user_id", "is_deleted"),
        ]


__all__ = [
    "AgentSession",
    "AgentMessage",
    "AgentSkill",
    "AgentSkillFile",
    "AgentArtifact",
    "AgentDailyBrief",
    "AgentQuickAction",
    "AgentQuickActionExample",
    "AgentQuickActionCategory",
    "AgentQuickActionLink",
    "AgentProfession",
    "AgentUserSubscription",
    "AgentWorkflow",
]
