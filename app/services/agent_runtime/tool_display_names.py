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
    # 向量检索
    "vector_search_standards": "语义搜索标准",
    "vector_search_chapters": "语义搜索章节",
    "vector_hybrid_search": "混合搜索",
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
    # 视觉理解
    "vision_inspect": "视觉理解",
    # 文件系统（deepagents 自带）
    "ls": "列出文件",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "glob": "文件匹配",
    "grep": "内容搜索",
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
}


def get_tool_display_name(tool_name: str) -> str:
    """
    获取工具的中文显示名。

    Args:
        tool_name: 工具原始名称（如 standard_query、generate_ark_video）

    Returns:
        中文显示名，未映射时返回原名
    """
    return _TOOL_DISPLAY_MAP.get(tool_name, tool_name)
