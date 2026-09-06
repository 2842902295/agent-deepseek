from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentSession(BaseModel, TimestampMixin):
    """智能体会话"""

    id = fields.IntField(pk=True, description="主键ID")

    session_key = fields.CharField(max_length=64, unique=True, description="会话 key，前后端统一标识")
    user_id = fields.IntField(null=True, description="所属用户ID（跨库不做外键）")
    workflow_key = fields.CharField(max_length=64, null=True, description="关联工作流 key（画板内发起的会话）；null=通用问答")
    expert_key = fields.CharField(max_length=64, null=True, description="会话召唤的专家 key（agent_expert.expert_key）；null=通用会话。@专家名 驻留式绑定")

    title = fields.CharField(max_length=128, default="新任务", description="任务标题")
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
    thinking = fields.TextField(null=True, description="思考过程（dsh 迁移后停用，历史数据保留）")
    tool_steps_json = fields.JSONField(null=True, description="工具调用轨迹（旧三段式，历史数据保留；新消息写 process_json）")
    process_json = fields.JSONField(null=True, description="过程时间线条目 [{id,kind:reasoning|text|tool_call|tool_result|todo|compaction|questionnaire,...}]")
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
        description="builtin 内置 / derived 凝练 / curated 收录（上传与发现合并）；「官方」分类已废除，存量 official 行按 curated 对待",
    )
    origin_session_id = fields.IntField(null=True, description="derived 时来源会话ID")

    user_id = fields.IntField(null=True, description="所属用户ID；null 表示公共能力")
    is_enabled = fields.IntField(default=1, description="1 启用 0 停用")
    min_tier_code = fields.JSONField(
        null=True,
        default=None,
        description="可见档位白名单（agent_role_tier.tier_code 数组）；NULL/空数组=全员可见；显式多选，档位间无包含关系，勾选哪些档位就只有这些档位的用户可见；仅管理员可改",
    )
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
    icon = fields.TextField(
        null=True,
        description="图标：单个 <svg> 元素源码或图片 data URI（agent 创建技能时生成/用户上传）；NULL=前端显示兜底图标",
    )
    category = fields.CharField(
        max_length=32,
        null=True,
        description="分类（商店导航用；词表见 agent_skill.py::SKILL_CATEGORIES，NULL 按「其他」展示）",
    )
    is_featured = fields.IntField(
        default=0,
        description="1 精选技能（商店顶部精选区展示，仅管理员可设置）",
    )
    example_questions = fields.JSONField(
        null=True,
        description="快捷提问（JSON 字符串数组）：展示在卡片上，用户点击即添加该技能并把问题填入输入框；建议≤6条、单条≤100字",
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


class AgentSkillCategory(BaseModel, TimestampMixin):
    """技能商店分类词表（管理员/system-admin 可增删改；首启由 skill_seed 播种默认词表）。

    agent_skill.category 存这里的 name；词表外的取值由后端归一为 NULL（前端落「其他」兜底桶）。
    """

    id = fields.BigIntField(pk=True, description="主键ID")
    name = fields.CharField(max_length=32, unique=True, description="分类名（展示与存储口径）")
    sort_order = fields.IntField(default=0, description="商店导航排序，小的在前")
    icon = fields.TextField(
        null=True,
        description="分类图标：单个 <svg> 元素源码或图片 data URI（与 agent_skill.icon 同族）；NULL=前端显示兜底图标",
    )

    class Meta:
        table = "agent_skill_category"
        table_description = "技能商店分类词表"


class AgentSkillUserPref(BaseModel, TimestampMixin):
    """用户技能个人偏好（商店模型）：is_added=是否已添加到「我的技能」，is_enabled=启用/禁用。

    两个状态正交：禁用=完全剔除（不加载、@ 不可调用），移除=退回商店未添加态。
    只影响自己，不影响全局 is_enabled（商店上架/下架）。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    skill_key = fields.CharField(max_length=64, description="技能 key（对应 agent_skill.skill_key）")
    is_enabled = fields.IntField(default=1, description="1 启用 0 禁用（禁用=完全不加载、@ 不可调用）；无记录=默认启用")
    is_added = fields.IntField(default=1, description="1 已添加到我的技能 0 未添加；无记录=默认未添加（本人创建的技能除外）。"
                                                       "行默认值 1 仅在新建行时生效，而建行只发生在「已添加」语境（我的技能页操作）")

    class Meta:
        table = "agent_skill_user_pref"
        table_description = "Agent 技能用户个人启停偏好"
        unique_together = [("user_id", "skill_key")]
        indexes = [
            ("user_id",),
        ]


class AgentConnector(BaseModel, TimestampMixin):
    """MCP 服务连接器：用户/管理员添加的外部 MCP server，agent 构建时自动加载其工具。

    商店模型与技能一致但无可见范围（visibility）机制：
    is_enabled=1（管理员上架）→ 全员商店可见；默认 0 未上架仅创建者可见。
    api_key 明文入库（同 agent_model_block 口径），任何响应/日志一律脱敏。
    """

    id = fields.IntField(pk=True, description="主键ID")

    connector_key = fields.CharField(max_length=64, unique=True, description="连接器 key（conn_ 前缀随机生成；同时作为 MCP server name 与 pref 关联键）")
    name = fields.CharField(max_length=64, description="显示名")
    description = fields.CharField(max_length=1000, null=True, description="简短描述")

    transport = fields.CharField(max_length=16, description="MCP 传输方式：sse / streamable_http")
    url = fields.CharField(max_length=512, description="MCP server 地址（仅 http/https）")
    kind = fields.CharField(
        max_length=16,
        default="connector",
        description="类别：connector 通用 MCP 连接器 / dataset 数据集（经 MCP 接入的数据源，产品上独立维度展示；底层机制完全一致）",
    )
    credential_mode = fields.CharField(
        max_length=16,
        default="shared",
        description="凭据类型：none 无需凭据 / shared 共享凭据（api_key 字段，管理员提供全员共用）/ personal 用户自带（每人填自己的，存 agent_connector_credential）",
    )
    api_key = fields.CharField(max_length=256, null=True, description="共享凭据（创建者/管理员提供，可选兜底；用户个人凭据在 agent_connector_credential，解析顺序本人>共享。明文入库，全程脱敏回显）")

    icon = fields.TextField(
        null=True,
        description="图标：单个 <svg> 元素源码或图片 data URI（与 agent_skill.icon 同族）；NULL=前端显示兜底图标",
    )
    user_id = fields.IntField(null=True, description="创建者用户ID")
    is_enabled = fields.IntField(default=0, description="商店上架：1 上架全员可见 0 未上架仅创建者可见（默认未上架，仅管理员可改）")
    min_tier_code = fields.JSONField(
        null=True,
        default=None,
        description="可见档位白名单（agent_role_tier.tier_code 数组）；NULL/空数组=全员可见；显式多选，档位间无包含关系，勾选哪些档位就只有这些档位的用户可见；仅管理员可改",
    )
    example_questions = fields.JSONField(
        null=True,
        description="快捷提问（JSON 字符串数组）：展示在卡片上，用户点击即添加该连接器/数据集并把问题填入输入框；建议≤6条、单条≤100字",
    )

    class Meta:
        table = "agent_connector"
        table_description = "MCP 服务连接器"
        indexes = [
            ("user_id", "is_enabled"),
            ("connector_key",),
        ]


class AgentConnectorUserPref(BaseModel, TimestampMixin):
    """用户连接器个人偏好（商店模型，与 AgentSkillUserPref 同构）：
    is_added=是否已添加到「我的连接器」，is_enabled=启用/禁用（正交）。
    只影响自己的 agent 工具加载，不影响全局 is_enabled（商店上架/下架）。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    connector_key = fields.CharField(max_length=64, description="连接器 key（对应 agent_connector.connector_key）")
    is_enabled = fields.IntField(default=1, description="1 启用 0 禁用（禁用=agent 不加载该连接器工具）；无记录=默认启用")
    is_added = fields.IntField(default=1, description="1 已添加到我的连接器 0 未添加；无记录=默认未添加（本人创建的连接器除外）。"
                                                          "行默认值 1 仅在新建行时生效，而建行只发生在「已添加」语境")

    class Meta:
        table = "agent_connector_user_pref"
        table_description = "Agent 连接器用户个人启停偏好"
        unique_together = [("user_id", "connector_key")]
        indexes = [
            ("user_id",),
        ]


class AgentConnectorCredential(BaseModel, TimestampMixin):
    """连接器的用户个人凭据（一人一份，一对一）。

    上架共享的连接器只共享定义（名称/地址/传输方式），凭据通常是用户私有的：
    每人把自己的 api_key 存在这里。加载/试连解析顺序 = 本人凭据 > 连接器行的共享凭据。
    明文入库（同 agent_connector.api_key 口径），响应只回 hasMyKey 布尔，日志禁止出现。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    connector_key = fields.CharField(max_length=64, description="连接器 key（对应 agent_connector.connector_key）")
    api_key = fields.CharField(max_length=256, description="个人凭据（明文入库，全程脱敏回显）")

    class Meta:
        table = "agent_connector_credential"
        table_description = "Agent 连接器用户个人凭据"
        unique_together = [("user_id", "connector_key")]
        indexes = [
            ("user_id",),
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


class AgentRoleTier(BaseModel, TimestampMixin):
    """用户档位定义（实体可见性·显式多选白名单）：三档基线 all=普通用户(rank 0) / paid=付费用户(rank 1) / gov=主管部门(rank 2)。

    2026-08-27 起档位间**无包含关系**：实体 min_tier_code 存可见档位 code 数组，
    勾选哪些档位就只有持这些档位的用户可见。用户档位集合 = 其所持角色（role_codes 命中）
    映射到的全部档位；未持任何标记角色 = 空集（仅全员可见实体）；R_SUPER/R_ADMIN 恒全可见（运行期哨兵）。
    tier_rank 仅作展示排序。启动幂等播种、只补缺失档不回写；档位增删改由 system-admin 经 admin_save_record 完成。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    tier_code = fields.CharField(max_length=16, unique=True, description="档位 code（实体 min_tier_code 引用它），如 all / paid / gov")
    tier_name = fields.CharField(max_length=32, description="档位展示名，如 普通用户 / 付费用户 / 主管部门")
    tier_rank = fields.IntField(default=0, description="档位序：仅作展示排序，不参与可见性判定；基线 all=0 / paid=1 / gov=2")
    role_codes = fields.JSONField(null=True, description='映射到该档位的角色 code 数组，如 ["R_PAID"]；用户持有其中任一角色即达到该档')

    class Meta:
        table = "agent_role_tier"
        table_description = "用户档位定义（实体可见性分档）"
        indexes = [
            ("tier_rank",),
        ]


class AgentQuickActionExample(BaseModel, TimestampMixin):
    """快捷功能案例：每个快捷功能可关联多个使用案例（历史会话）"""

    id = fields.IntField(pk=True, description="主键ID")

    action_id = fields.IntField(description="已废弃：原所属快捷功能ID；技能整合后归属看 skill_key")
    skill_key = fields.CharField(max_length=64, null=True, description="所属技能 key（agent_skill.skill_key；技能整合后的案例归属）")
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


class AgentExpert(BaseModel, TimestampMixin):
    """专家：「人设 + 方法论 + 技能包 + 连接器」的封装体，会话级召唤（@专家名 驻留绑定）。

    商店模型与技能/连接器同构：is_enabled=1（上架）全员可见；默认 0 未上架仅创建者（user_id）可见。
    会话经 agent_session.expert_key 绑定；instructions 在 agent 构建期注入系统提示词，
    skill_keys/connector_keys 在绑定会话内运行时并入生效集（不落个人 is_added）。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    expert_key = fields.CharField(max_length=64, unique=True, description="专家 key（exp_ 前缀；@召唤与会话绑定引用键）")
    name = fields.CharField(max_length=32, description="专家名，如：标准编制专家")
    icon = fields.CharField(max_length=128, null=True, description="图标标识（iconify 图标名）")
    description = fields.CharField(max_length=200, null=True, description="卡片一句话简介")

    instructions = fields.TextField(null=True, description="人设与方法论提示词：agent 构建期注入系统提示词（专业定位/工作方法/交付风格）")
    welcome_message = fields.TextField(null=True, description="绑定会话首屏欢迎语（纯前端渲染，不进提示词）")

    skill_keys = fields.JSONField(null=True, description="绑定技能 key 数组（agent_skill.skill_key；绑定专家的会话运行时并入生效集，不落 is_added）")
    connector_keys = fields.JSONField(null=True, description="绑定连接器 key 数组（agent_connector.connector_key；同上运行时并入）")

    category = fields.CharField(max_length=32, null=True, description="专家中心分组（行业/领域）")
    user_id = fields.IntField(null=True, description="创建者用户ID（null=官方预设专家）")

    sort_order = fields.IntField(default=0, description="排序，越小越靠前")
    is_enabled = fields.IntField(default=0, description="商店上架：1 上架全员可见 0 未上架仅创建者可见")
    min_tier_code = fields.JSONField(
        null=True,
        default=None,
        description="可见档位白名单（agent_role_tier.tier_code 数组）；NULL/空数组=全员可见；显式多选，档位间无包含关系，勾选哪些档位就只有这些档位的用户可见；仅管理员可改",
    )

    created_by = fields.IntField(null=True, description="创建者用户ID")
    example_questions = fields.JSONField(
        null=True,
        description="快捷提问（JSON 字符串数组）：展示在卡片上，用户点击即添加该专家并把问题填入输入框；建议≤6条、单条≤100字",
    )

    class Meta:
        table = "agent_expert"
        table_description = "Agent 专家"
        indexes = [
            ("user_id", "is_enabled"),
            ("is_enabled", "sort_order"),
        ]


class AgentExpertUserPref(BaseModel, TimestampMixin):
    """用户专家个人偏好（商店模型，与 AgentSkillUserPref/AgentConnectorUserPref 同构）：
    is_added=是否已添加到「我的专家」，is_enabled=启用/禁用（正交）。
    禁用=不可被召唤（@ 不命中、面板不展示）；只影响自己，不影响全局 is_enabled。
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户ID")
    expert_key = fields.CharField(max_length=64, description="专家 key（对应 agent_expert.expert_key）")
    is_enabled = fields.IntField(default=1, description="1 启用 0 禁用（禁用=不可召唤）；无记录=默认启用")
    is_added = fields.IntField(default=1, description="1 已添加到我的专家 0 未添加；无记录=默认未添加（本人创建的专家除外）。"
                                                      "行默认值 1 仅在新建行时生效，而建行只发生在「已添加」语境")

    class Meta:
        table = "agent_expert_user_pref"
        table_description = "Agent 专家用户个人偏好"
        unique_together = [("user_id", "expert_key")]
        indexes = [
            ("user_id",),
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


class AgentUserCreditQuota(BaseModel, TimestampMixin):
    """用户积分配额（BRAND_VARIANT=generic 生效，standard 不检查）：每用户一行，无记录=默认配额。

    消费方 billing/quota.py::check_quota 与 dashboard /credit-quotas 端点（原生 SQL）。
    已用积分 = SUM(agent_usage_log.credits)，不在本表，改配额不影响已用。
    表结构以 init_app.py 的 CREATE TABLE 为准（user_id 为主键，无自增 id 列）。
    """

    user_id = fields.BigIntField(pk=True, generated=False, description="用户ID（users.id），主键")
    quota = fields.BigIntField(default=200000, description="积分配额（非负）")

    class Meta:
        table = "agent_user_credit_quota"
        table_description = "用户积分配额（generic 品牌）"


class AgentWorkflow(BaseModel, TimestampMixin):
    """Agent 共享工作流（人和 Agent 共读共写的 Vue Flow JSON）"""

    id = fields.BigIntField(pk=True, description="主键ID")

    workflow_key = fields.CharField(max_length=64, unique=True, description="工作流 key（wf_ + hex）")
    session_key = fields.CharField(max_length=64, null=True, description="创建来源会话（仅记录，不绑定）")
    user_id = fields.IntField(null=True, description="所属用户ID")

    title = fields.CharField(max_length=128, default="未命名工作流", description="工作流标题")
    # 板型：board=节点连线流程编排（默认）；html=应用制作（agent 在 apps/{workflow_key}/ 目录开发多文件
    # HTML 应用，前端 iframe 画布渲染）。读取侧一律 `wf.board_type or "board"` 兜底防脏数据。
    board_type = fields.CharField(max_length=16, default="board", description="板型：board 流程编排 / html 应用制作")
    # html 型专用：入口 index.html 是否已发布过（publish_html_board 置 1，发布即就绪、只升不降）。
    # 必须是 DB 持久标志而不能只查本机文件：DB 共享而 .agent_workspace 不共享的跨机部署里，
    # 本地实例查不到服务器上构建的文件，纯文件判定会把已发布的板误判为空板被清理掉。
    entry_ready = fields.IntField(default=0, description="应用制作入口就绪（index.html 已发布过）：0 否 1 是")
    # 应用制作列表卡预览素材：publish_html_board 发布时从 index.html 提取（title/tagline/accent 主题色）落库。
    # 必须落库：列表渲染要读它，而 DB 共享、工作目录不共享的跨机部署里，列表接口读不到另一台实例上的文件。
    html_preview = fields.JSONField(null=True, description="应用制作预览素材 {title, tagline, accent}（发布时从 index.html 提取）")
    # html 型专用：分享开关。开启后访客可经 /share/{workflow_key} 打开使用这个应用
    # （数据层写回 / 页内 AI 通道照常，但不注入「编辑文字」脚本，不能编辑看板本身）。
    share_on = fields.IntField(default=0, description="应用制作分享开关：0 关 1 开（仅 html 型有效）")
    # html 型专用：分享模式。share_on 开启的前提下：0=仅登录用户可打开（访客需有效登录态），
    # 1=免登录公开（任意访客匿名打开）。匿名访问登录模式分享时 share-view 返回 needLogin 由前端引导登录。
    share_public = fields.IntField(default=0, description="应用制作分享模式：0 仅登录用户 1 免登录公开（仅 share_on 开启时有效）")
    # html 型专用：当前正在查看的发布存档版本号（agent_app_file version>=1）。
    # 版本语义改造后存档只在用户点「发布」时产生：agent 发布仅更新画面（工作态），
    # NULL = 未固化的工作态（不对应任何存档）；切换版本即改此指针 + 目标版文件还原。
    app_version = fields.IntField(null=True, description="应用制作当前查看的存档版本号；NULL=未固化工作态")
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


class AgentWorkflowVersion(BaseModel, TimestampMixin):
    """工作流版本存档——节点流程编排（board 型）的全量快照，「回到上一版」的系统级真相源。

    节点板每次 version 递增的写入（人 / agent / 回滚）之后追加一行当前态快照；保留最近若干版。
    应用制作（html 型）不在此表——它的应用文件与发布存档在 agent_app_file（version>=1 为存档），
    两类板型的「版本列表 / 回滚」由 agent_workflow.py 的统一端点按 board_type 分派。"""

    id = fields.BigIntField(pk=True, description="主键ID")

    workflow_key = fields.CharField(max_length=64, description="工作流 key")
    version_no = fields.IntField(description="存档时的 agent_workflow.version")
    snapshot = fields.JSONField(description="全量快照 {title, nodes, edges, viewport}")
    editor = fields.CharField(max_length=8, null=True, description="产生该版的写入者 human/agent")

    class Meta:
        table = "agent_workflow_version"
        table_description = "工作流版本存档（节点板全量快照）"
        indexes = [
            ("workflow_key", "version_no"),
        ]


class AgentAppFile(BaseModel, TimestampMixin):
    """应用制作（html 型工作流）应用文件——发布态进 DB，跨机按需物化回磁盘（与 agent_skill_file 同族）。

    version 双语义：
    - 0 = 活动集：当前工作态 + 运行期 /save 写回的数据 json；跨机物化与写回都操作它；
    - >=1 = 不可变版本存档：用户点「发布」固化一版（切换版本前若有未固化改动也会自动备份一版），
      是版本切换的系统级真相源，保留最近 5 版。agent 发布只刷新活动集不产存档。

    uploads/（用户上传文件，单文件可到 10GB）一律不入库——留在磁盘，跨机不可见；
    单文件超过入库上限（见 agent_workflow.py::_APP_FILE_DB_MAX_BYTES）的也只留磁盘。
    agent 的文件读写仍然面对 apps/{workflow_key}/ 磁盘目录，入库/物化对 agent 完全透明。"""

    id = fields.BigIntField(pk=True, description="主键ID")

    workflow_key = fields.CharField(max_length=64, description="所属工作流 key（agent_workflow.workflow_key）")
    path = fields.CharField(max_length=512, description="文件相对路径（正斜杠），如 index.html、data/store.json")
    content = fields.BinaryField(description="文件字节")
    size = fields.IntField(default=0, description="文件字节数")
    version = fields.IntField(default=0, description="0=活动集；>=1 发布存档版本号")
    wf_version = fields.IntField(default=0, description="存档对应的 agent_workflow.version（活动集无意义恒 0）")
    editor = fields.CharField(max_length=8, null=True, description="存档固化者 human/agent（活动集无意义恒 NULL）")

    class Meta:
        table = "agent_app_file"
        table_description = "应用制作文件（BLOB 存储 + 发布存档）"
        indexes = [
            ("workflow_key", "version"),
        ]


class AgentAppRow(BaseModel, TimestampMixin):
    """应用制作（html 型工作流）行数据层——应用的用户数据统一存这里，一行 = 应用里的一条业务记录。

    定位等同 vec_item 的「大表 + 区分列」模式：不为每个应用建真表（应用数据结构随对话生长，
    避免 DDL 爆炸），(workflow_key, tbl, row_key) 三元组定位一行，data 列装任意 JSON。
    - tbl：应用内逻辑集合名（应用自定义，如 members / records）；`$` 开头为平台保留
      （`$acl` = 写读保护清单，见 html_app.py 行通道）
    - 行级写回替代 json 整文件覆盖：多用户各写各的行互不冲突
    - 不参与版本存档 / 回滚（与 agent_app_file 解耦）：回滚只还原页面代码，不碰数据
    - 删板即删行（delete_workflow 连带清理）"""

    id = fields.BigIntField(pk=True, description="主键ID")

    workflow_key = fields.CharField(max_length=64, description="所属工作流 key（agent_workflow.workflow_key）")
    tbl = fields.CharField(max_length=64, description="应用内逻辑集合名；$ 开头平台保留（$acl 保护清单）")
    row_key = fields.CharField(max_length=191, description="行键（应用自定，多用户按人数据建议 {visitorId}: 前缀）")
    data = fields.JSONField(description="行数据（任意 JSON）")
    updated_by = fields.IntField(null=True, description="最后写入者用户ID；匿名访客写入为 NULL")

    class Meta:
        table = "agent_app_row"
        table_description = "应用制作行数据层（一行=一条业务记录）"
        unique_together = [("workflow_key", "tbl", "row_key")]
        indexes = [
            ("workflow_key", "tbl"),
        ]


__all__ = [
    "AgentSession",
    "AgentMessage",
    "AgentSkill",
    "AgentSkillFile",
    "AgentSkillUserPref",
    "AgentSkillCategory",
    "AgentConnector",
    "AgentConnectorUserPref",
    "AgentConnectorCredential",
    "AgentArtifact",
    "AgentDailyBrief",
    "AgentQuickAction",
    "AgentQuickActionExample",
    "AgentQuickActionCategory",
    "AgentQuickActionLink",
    "AgentExpert",
    "AgentExpertUserPref",
    "AgentUserSubscription",
    "AgentUserCreditQuota",
    "AgentWorkflow",
    "AgentWorkflowVersion",
    "AgentAppFile",
    "AgentAppRow",
    "AgentRoleTier",
]
