"""
工具名中文映射表

后端统一维护工具名 → 中文显示名的映射，SSE 流和 API 响应中自动添加 tool_display 字段。
"""

_TOOL_DISPLAY_MAP = {
    # 数据库查询（仅限 standard_ 前缀标准表）
    "standard_query": "查询标准数据",
    "standard_tables": "查看标准表列表",
    "standard_schema": "查看标准表结构",
    "get_standard_chapters": "读取标准章节",
    "get_near_chapters": "读取邻近章节",
    "get_cached_indicators": "读取指标缓存",
    "search_candidate_standards": "搜索候选标准",
    # 向量检索（统一向量库体系）
    "vector_search_standards_ob": "语义搜索标准",
    "vector_search_chapters": "语义搜索章节",
    "vector_compare_standards": "标准章节对比",
    "vector_hybrid_search": "混合搜索",
    # 系统数据集（条款集 / 术语集 / 标准元数据集，app/mcp_bridge/datasets.py）
    "search_terms_vector": "语义搜索术语",
    "search_terms": "查询术语",
    "search_standards": "查询标准元数据",
    # 向量库管理
    "vector_lib_list": "列出向量库",
    "vector_lib_search": "向量库搜索",
    "vector_lib_create": "新建向量库",
    "vector_lib_add_items": "写入向量库条目",
    "vector_lib_delete_items": "删除向量库条目",
    # 文档解析
    "parse_with_mineru": "解析文档",
    # 图像生成
    "generate_qwen_image": "生成图像",
    "generate_gpt_image": "生成图像",
    "generate_apipod_image": "生成图像",
    # 视频生成
    "generate_ark_video": "生成视频",
    "query_ark_video_task": "查询视频任务",
    "generate_happyhorse_video": "生成视频",
    "query_happyhorse_video_task": "查询视频任务",
    "generate_apipod_video": "生成视频",
    "query_apipod_video_task": "查询视频任务",
    "generate_wan_video": "生成视频",
    "query_wan_video_task": "查询视频任务",
    # 视觉理解
    "vision_inspect": "视觉理解",
    # 文件系统（deepagents 自带）
    "ls": "列出文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    # Shell
    "execute": "执行命令",
    # 任务规划
    "write_todos": "规划任务",
    # 子 Agent
    "task": "调度子任务",
    # 产物注册
    "register_artifact": "注册产物",
    # 记忆管理
    "manage_memory": "管理记忆",
    "search_memory": "搜索记忆",
    # 个人知识库
    "kb_list_collections": "列出知识库",
    "kb_query_collection": "查询知识库",
    "kb_add_documents": "添加文档",
    "kb_delete_documents": "删除文档",
    "kb_update_document": "更新文档",
    "kb_get_document": "获取文档",
    # 历史对话回溯（chat-history 子 agent）
    "search_chat_history": "搜索历史消息",
    "list_recent_sessions": "列出最近会话",
    "get_session_messages": "获取会话消息",
    "get_message_detail": "读取消息全文",
    # 系统管理（system-admin 子 agent，超管专属，通用工具版）
    "admin_tables": "查看可管理表",
    "admin_table_schema": "查看系统表结构",
    "admin_sql": "查询系统数据",
    "admin_save_record": "保存系统记录",
    "admin_delete_record": "删除系统记录",
    "admin_grant_role": "角色授权",
    # 技能管理（统一 4 工具）
    "skill_read": "读取技能",
    "skill_save": "保存技能",
    "skill_delete": "删除技能",
    "skill_install": "安装技能",
    # 时间
    "get_current_time": "获取当前时间",
    # 联网搜索（MCP 或其他）
    "bailian_web_search": "联网搜索",
    "web_search": "联网搜索",
    "tavily_search": "联网搜索",
    "brave_web_search": "联网搜索",
    "brave_local_search": "本地搜索",
    # 其他常见 MCP 工具
    "fetch": "网页抓取",
    "search": "搜索",
    # ── dsh 运行时内置工具（dsh-tool-fs / bash / todo / skill 插件的真实工具名）──
    "bash": "执行命令",
    "read": "读取文件",
    "write": "写入文件",
    "edit": "编辑文件",
    "glob": "查找文件",
    "grep": "搜索内容",
    "todo_write": "更新任务清单",
    "skill": "加载技能",
    # ── 子代理委派（通用委派工具 + 专属子代理，见 qa_agent._subagent_defs）──
    "subagent": "委派子任务",
    "quality-scout": "委派质量侦察",
    "skill-management": "委派技能管理",
    "knowledge-base": "委派知识库管理",
    "chat-history": "委派历史回溯",
    "system-admin": "委派系统管理",
    # ── 知识库（seekdb，真实注册名；上方 kb_* 旧键为 deepagents 时代遗留）──
    "kb_search": "检索知识库",
    "kb_create": "新建知识库条目",
    "kb_update": "更新知识库条目",
    "kb_delete": "删除知识库条目",
    "kb_merge": "合并知识库条目",
    "kb_split": "拆分知识库条目",
    # ── 向量检索（OceanBase 版真实注册名）──
    "vector_search_standards_ob": "语义搜索标准",
    "vector_compare_standards": "对比标准",
    # ── 图表 / 产物 / 技能清单 ──
    "create_chart": "生成图表",
    "skill_list": "列出技能",
    # ── 工作流画板与定时任务 ──
    "create_workflow_board": "创建板子",
    "read_workflow": "读取工作流",
    "edit_workflow_board": "编辑工作流画板",
    "publish_html_board": "发布应用",
    "rollback_workflow_version": "回滚工作流版本",
    "create_scheduled_task": "创建定时任务",
    "list_scheduled_tasks": "查看定时任务",
    "delete_scheduled_task": "删除定时任务",
}


def strip_tool_prefix(tool_name: str) -> str:
    """
    剥掉 MCP 传输前缀，返回工具基础名。

    dsh 阶段经 MCP 桥挂载的工具带传输前缀（mcp__stdtools__X / mcp__websearch__X /
    mcp__dataset_meta__X / mcp__conn_<key>__X），剥掉前两段得到原始工具名。
    """
    if not tool_name:
        return tool_name
    name = tool_name
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[2]:
            name = parts[2]
    return name


def get_tool_display_name(tool_name: str) -> str:
    """
    获取工具的中文显示名。

    Args:
        tool_name: 工具原始名称（如 standard_query、mcp__stdtools__standard_query）

    Returns:
        中文显示名，未映射时返回原名。
    """
    if not tool_name:
        return tool_name
    return _TOOL_DISPLAY_MAP.get(strip_tool_prefix(tool_name), tool_name)
