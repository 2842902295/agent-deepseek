"""deepagents read_file 视频读取补丁：base64 内联 → 公网 URL + video_url 块。

背景：
- deepagents 的 FilesystemMiddleware.read_file 读二进制文件时，把整个文件以
  `{"type": "file", "base64": ..., "mime_type": ...}` 块塞进 ToolMessage（未安装
  PyAV [video] extra 时视频也走这条通用分支）。
- 视频动辄数 MB~数十 MB，base64 后请求体远超 DashScope 的 6MB 上限
  （400 "Exceeded limit on max bytes to request body"），且巨型 base64 还会
  永久落进 checkpoint 历史，之后每轮都重发。
- 本项目主模型（qwen3.8-max 等 CHAT 块）原生支持 video_url 多模态直传
  （用户上传视频走的就是这条路），因此工作区视频应发布成 PUBLIC_BASE_URL
  公网链接、以 video_url 块交给模型，消息里只留一行 URL。

做法：一次性 patch `FilesystemMiddleware._create_read_file_tool`（create_deep_agent
内部为主 agent 与各子 agent 硬编码构造该 middleware，无法注入子类，只能类级补丁），
把生成的 read_file 工具包一层：结果里出现「base64 视频块」（type="video"，或
type="file" 且 mime 为 video/*）就解码、发布公网 URL、替换为 text 说明 + video_url 块。
图片/PDF/文本行为不变。

兼容性（_VISION_SUPPORTED=false 的纯文本模型块）：deepagents 的 profile 门控对本项目
模型不生效（profile 只有 max_input_tokens，缺省字段默认"支持"），视频块不会被它拦下。
因此本补丁按 `effective_chat_supports_vision()`（本次请求生效 chat 块的
{BLOCK}_VISION_SUPPORTED；按角色模型配置下跟随角色块，未设 profile 回退全局激活块）
门控：视觉模型 → video_url 直传；纯文本模型 → 不附加视频块，回文本说明引导改用
vision_inspect（VISION 角色兜底，图片视频同一个工具；未配置 VISION 角色时才回
"无法查看"的拒绝文案）。图片读取不受本补丁影响。

已实测 DashScope 接受 tool 消息中的 video_url 块并能直接理解视频内容。
"""

import asyncio
import base64
import functools
from typing import Optional

from loguru import logger

# 工作区视频发布上限（与 qa.py 聊天直传视频同一量级；更大文件提示用户裁剪）
_MAX_WORKSPACE_VIDEO_BYTES = 100 * 1024 * 1024

_PATCHED_FLAG = "_cesi_video_read_patched"


def _video_file_blocks(msg) -> list[int]:
    """返回 ToolMessage.content 中「base64 视频块」的下标列表。

    deepagents 按扩展名映射块类型：已知视频扩展名（.mp4 等）→ type="video"，
    未知二进制扩展名才落到 type="file"（mime 区分），两种都要拦。
    """
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return []
    idxs = []
    for i, b in enumerate(content):
        if not (isinstance(b, dict) and "base64" in b):
            continue
        btype = b.get("type")
        if btype == "video" or (btype == "file" and str(b.get("mime_type") or "").startswith("video/")):
            idxs.append(i)
    return idxs


def _publish_video_block(block: dict, path_hint: str) -> list[dict]:
    """把单个 video base64 块换成 [text 说明, video_url] 块；失败时返回 text 说明。

    非视觉主模型（{BLOCK}_VISION_SUPPORTED=false）不附加任何视频块，只回文本说明：
    deepagents 的 profile 门控对本项目不生效（profile 只有 max_input_tokens，缺省
    字段默认"支持"），若不拦，视频块会照发给纯文本模型（小视频 data URL 被拒/被
    忽略，大视频直接爆请求体上限）。与 qa.py 对用户上传视频的处理保持一致：
    纯文本模型下视频无兜底，告知用户暂无法分析。
    """
    from app.api.v1.ai.upload import publish_conversation_media_sync
    from app.langchain.role_model_profile import effective_chat_supports_vision

    if not effective_chat_supports_vision():
        from app.langchain.config import has_role

        if has_role("VISION"):
            # 主模型不支持视频时引导走 VISION 角色工具（与图片同一个 vision_inspect 工具）
            return [
                {
                    "type": "text",
                    "text": (
                        f"[read_file] 当前主模型不支持视频理解，视频内容未附加。"
                        f"请改用 vision_inspect 工具查看该视频：file={path_hint}，"
                        "question=你具体想了解的问题（视觉模型会观看视频并给出文字回答）。"
                    ),
                }
            ]
        return [
            {
                "type": "text",
                "text": (f"[read_file] 当前主模型不支持视频理解且未配置视觉模型，视频 {path_hint} 无法查看。请勿尝试用其他方式读取该视频，可在回复中告知用户暂时无法直接分析视频内容。"),
            }
        ]

    mime = str(block.get("mime_type") or "video/mp4")
    ext = mime.split("/")[-1] if "/" in mime else "mp4"
    try:
        raw = base64.b64decode(block["base64"], validate=True) if isinstance(block["base64"], str) else bytes(block["base64"])
    except Exception as e:
        logger.warning(f"[media_read_patch] 视频 base64 解码失败 {path_hint}: {type(e).__name__}: {e}")
        return [{"type": "text", "text": f"[read_file] 视频 {path_hint} 解码失败，无法查看。"}]
    if len(raw) > _MAX_WORKSPACE_VIDEO_BYTES:
        logger.warning(f"[media_read_patch] 工作区视频过大，跳过发布: {path_hint}（{len(raw) // 1048576}MB）")
        return [{"type": "text", "text": f"[read_file] 视频 {path_hint} 过大（{len(raw) // 1048576}MB > 100MB），无法直接查看，请先用 shell 裁剪后再读。"}]
    url = publish_conversation_media_sync(raw, mime, ext=f".{ext}")
    if not url:
        return [{"type": "text", "text": f"[read_file] 视频 {path_hint} 发布公网链接失败，本次无法查看（可稍后重试）。"}]
    return [
        {"type": "text", "text": f"工作区视频 {path_hint} 的内容就是下方 video_url 块（多模态视频，已完整附上）。请直接观看该视频并基于实际画面作答，不要依据文件名推测。"},
        {"type": "video_url", "video_url": {"url": url}},
    ]


def _convert_video_message(msg) -> Optional[object]:
    """msg 含视频 base64 块时就地替换并返回 msg；无则返回 None。同步发布（调用方已在工作线程）。"""
    idxs = _video_file_blocks(msg)
    if not idxs:
        return None
    path_hint = (getattr(msg, "additional_kwargs", None) or {}).get("read_file_path") or "该视频"
    content = list(msg.content)
    for i in sorted(idxs, reverse=True):
        content[i : i + 1] = _publish_video_block(content[i], path_hint)
    msg.content = content
    logger.info(f"[media_read_patch] read_file 视频已转公网 video_url: {path_hint}")
    return msg


async def _convert_video_message_async(msg):
    """异步路径：发布走线程池，不阻塞事件循环。"""
    idxs = _video_file_blocks(msg)
    if not idxs:
        return None
    path_hint = (getattr(msg, "additional_kwargs", None) or {}).get("read_file_path") or "该视频"
    content = list(msg.content)
    for i in sorted(idxs, reverse=True):
        block = content[i]
        new_blocks = await asyncio.to_thread(_publish_video_block, block, path_hint)
        content[i : i + 1] = new_blocks
    msg.content = content
    logger.info(f"[media_read_patch] read_file 视频已转公网 video_url: {path_hint}")
    return msg


def patch_filesystem_media_read() -> None:
    """幂等地给 FilesystemMiddleware 的 read_file 工具加视频 URL 化包装。"""
    from deepagents.middleware import filesystem as fs

    if getattr(fs.FilesystemMiddleware, _PATCHED_FLAG, False):
        return

    _orig_create = fs.FilesystemMiddleware._create_read_file_tool

    def _create_read_file_tool_with_video_url(self):
        tool = _orig_create(self)
        orig_func = getattr(tool, "func", None)
        orig_coro = getattr(tool, "coroutine", None)

        # functools.wraps 保留原函数签名（inspect.signature 会沿 __wrapped__ 解包）：
        # deepagents 的 read_file 带 runtime 注入参数，LangChain 按签名决定注入，
        # 包装函数若丢签名会导致 runtime 缺失直接 TypeError。
        @functools.wraps(orig_func)
        def _wrapped_func(*args, **kwargs):
            result = orig_func(*args, **kwargs)
            _convert_video_message(result)
            return result

        @functools.wraps(orig_coro)
        async def _wrapped_coro(*args, **kwargs):
            result = await orig_coro(*args, **kwargs)
            await _convert_video_message_async(result)
            return result

        try:
            if orig_func is not None:
                tool.func = _wrapped_func
            if orig_coro is not None:
                tool.coroutine = _wrapped_coro
        except Exception:
            # pydantic 校验拒绝赋值时退化为仅同步包装不生效也不报错（保持原行为）
            logger.warning("[media_read_patch] read_file 工具包装失败，视频仍走原 base64 路径")
        return tool

    fs.FilesystemMiddleware._create_read_file_tool = _create_read_file_tool_with_video_url
    setattr(fs.FilesystemMiddleware, _PATCHED_FLAG, True)
    logger.info("[media_read_patch] read_file 视频 URL 化补丁已应用")
