"""标准库图片地址解析与下载（回填定时任务 / MinerU 单条测试接口共用）。

背景：`standard_jgh_pdf_table` / `standard_jgh_pdf_formula` 的 `image` 字段历史上混存多种形态——

1. 裸 `<img>` 标签：`<img src="https://www.miitstdps.cn/admin-api/standard/jgh-bag/image/xxx.jpg" data-type="table" ...>`
2. 段落包裹的标签：`<p><img class="h-full" src="https://.../admin-api-factory/standard/jgh-bag/image/xxx.jpg" alt=""></p>`
3. 站内相对路径：`/oss/privateDomain/20230911/2022/xxx_image.png`
4. 空值 / CMS 占位图（spacer.gif）

旧实现直接 `备用host + image` 字符串相加，形态 1/2 会拼出
`http://dzsy.iyunwen.com%3Cimg%20src%3D%22https%3A//...` 这种**畸形 URL**（HTML 被塞进 host），
DNS 解析失败或连接被立即断开（`Server disconnected without sending a response`），
回填定时任务因此每轮白跑数万条、日志刷屏，还把真正可下载的公网地址埋没了。

本模块把「解析候选地址」与「按序下载」收口一处：

- `build_image_candidates()` → 合法、去重、按优先级排序的候选 URL 列表
- `fetch_first_available()`  → 逐个候选下载，首个成功即返回；全败抛 `ImageFetchError`，
  并区分「永久不可达」（全部候选 HTTP 4xx，图确实不存在）与「临时故障」
  （5xx / 超时 / 连不上 / 对端断开），供调用方决定是否熔断该记录
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

import httpx

# image 字段里的 src（HTML 形态）
_IMG_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# 合法 URL 里不该出现的字符（HTML 残留 / 空白 / 引号）
_INVALID_URL_CHARS_RE = re.compile(r"""[<>"'\s]""")
# host 允许的字符集（含 IPv6 方括号）
_INVALID_HOST_CHARS_RE = re.compile(r"[^0-9A-Za-z._\-\[\]:]")
# 被编码过的 HTML 残留（%3C=< %3E=> %22=" %27='）——旧拼接 bug 的指纹
_ENCODED_HTML_RE = re.compile(r"%3[cCeE]|%22|%27")
# CMS 占位图，下载回来没有解析价值
_JUNK_IMAGE_RE = re.compile(r"(?:spacer|blank|loading)\.gif$", re.IGNORECASE)

# 明确「这张图取不到」的 HTTP 状态（其余 4xx/5xx 一律按临时故障，下一轮再试）
_PERMANENT_STATUS = frozenset({400, 401, 403, 404, 405, 410, 451})

_EXT_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


class ImageEmptyError(Exception):
    """HTTP 200 但响应体为空（按临时故障处理，不当作"图不存在"）。"""


class InvalidImageUrlError(Exception):
    """候选地址不合法（非 http/https、含 HTML 残留字符等）。"""


@dataclass
class FetchedImage:
    """一次成功下载的结果。"""

    data: bytes
    filename: str
    content_type: str
    url: str
    # 命中前失败过的候选：(url, kind, error)
    attempts: list[tuple[str, str, str]] = field(default_factory=list)


class ImageFetchError(Exception):
    """所有候选地址都没取到图片。"""

    def __init__(self, attempts: list[tuple[str, str, str]], *, permanent: bool) -> None:
        self.attempts = attempts
        # permanent=True：全部候选都是「确定取不到」（HTTP 4xx），可熔断；否则属临时故障
        self.permanent = permanent
        super().__init__(self.summary())

    def summary(self, limit: int = 4) -> str:
        """把各候选的失败原因拼成一行简短摘要（供日志与 fill_log.error_msg）。"""
        if not self.attempts:
            return "无可用候选地址"
        parts = [f"{kind} {err[:80]} @{url[:120]}" for url, kind, err in self.attempts[:limit]]
        more = len(self.attempts) - limit
        if more > 0:
            parts.append(f"...另有 {more} 个候选")
        return " | ".join(parts)


def extract_image_srcs(image_field: str | None) -> list[str]:
    """从 `image` 字段提取图片地址：HTML 形态取标签里的 src，纯路径/纯 URL 原样返回。"""
    if not image_field:
        return []
    text = image_field.strip()
    if not text:
        return []
    if "<" in text:  # HTML 片段：可能包着 <p>/<img> 若干属性
        return [m.strip() for m in _IMG_SRC_RE.findall(text) if m.strip()]
    return [text]


def is_usable_url(url: str | None) -> bool:
    """候选地址合法性闸门：拦住旧拼接 bug 产出的畸形 URL（host 里带 HTML）。"""
    if not url:
        return False
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        return False
    if _INVALID_URL_CHARS_RE.search(u) or _ENCODED_HTML_RE.search(u):
        return False
    try:
        host = urlsplit(u).hostname or ""
    except ValueError:
        return False
    if not host or _INVALID_HOST_CHARS_RE.search(host):
        return False
    return True


def _basename(path_or_url: str) -> str:
    """取 URL / 路径的文件名部分（去 query、去锚点、解百分号编码）。"""
    tail = path_or_url.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
    return unquote(tail).strip()


def _join_base(base: str, path: str) -> str:
    """base（可能以 / 结尾）+ 相对路径 → 归一化 URL；无价值路径返回空串。

    DB 里的路径混杂真实空格与已编码好的 `%20`：直接 `quote()` 会把已编码的 `%` 再编码成
    `%25`（`%20`→`%2520`）导致 404；先 `unquote()` 还原字面值再 `quote()` 统一编码，
    两种来源都归一为正确形态。
    """
    clean = unquote((path or "").strip().lstrip("/"))
    if not clean or _JUNK_IMAGE_RE.search(clean):
        return ""
    if not base.strip():
        return ""
    return base.strip().rstrip("/") + "/" + quote(clean, safe="/")


def build_image_candidates(
    *,
    file_name: str | None = None,
    image: str | None = None,
    base_url: str | None = None,
    public_base_url: str | None = None,
    legacy_host: str | None = None,
) -> list[str]:
    """按优先级产出候选下载地址（去重、剔除非法值）。

    顺序：① 内网图片网关（`base_url` + file_name，最快）
         ② `image` 里 `<img src>` 的绝对地址（上游公网原址，路径前缀可能是
            `admin-api` 或 `admin-api-factory`，必须原样使用）
         ③ 公网镜像（`public_base_url` + file_name，file_name 与 src 文件名一一对应）
         ④ 旧站（`legacy_host` + 相对路径，仅 `image` 为站内相对路径时有值）

    任一前缀留空即跳过对应候选。
    """
    out: list[str] = []

    def push(u: str) -> None:
        if u and is_usable_url(u) and u not in out:
            out.append(u)

    fn = (file_name or "").strip()
    srcs = extract_image_srcs(image)
    # 多个 src 时，文件名与 file_name 对得上的优先
    if fn and len(srcs) > 1:
        srcs.sort(key=lambda s: 0 if _basename(s) == fn else 1)

    if base_url and fn:
        push(_join_base(base_url, fn))
    for src in srcs:
        if src.startswith(("http://", "https://")):
            push(src.strip())
    if public_base_url and fn:
        push(_join_base(public_base_url, fn))
    if legacy_host:
        for src in srcs:
            if not src.startswith(("http://", "https://")):
                push(_join_base(legacy_host, src))
    return out


def classify_fetch_error(exc: BaseException) -> str:
    """下载错误分类：`permanent`（该地址确定取不到）/ `transient`（临时故障，下轮再试）。

    只有 HTTP 4xx 算永久——超时、连不上、DNS 失败、对端断开、5xx 都可能是服务端一时故障，
    绝不能据此把记录永久熔断。
    """
    if isinstance(exc, InvalidImageUrlError):
        return "permanent"
    if isinstance(exc, httpx.HTTPStatusError):
        return "permanent" if exc.response.status_code in _PERMANENT_STATUS else "transient"
    return "transient"


def pick_filename(url: str, hint: str | None = None, content_type: str = "") -> str:
    """给 MinerU 上传用的文件名：优先 file_name（真实名），否则取 URL 尾段，无扩展名按 content-type 补。"""
    name = _basename(hint) if hint else ""
    if not name:
        name = _basename(url) or "image"
    if "." not in name:
        name += _EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower(), ".jpg")
    return name


async def fetch_first_available(
    client: httpx.AsyncClient,
    candidates: list[str],
    *,
    timeout: float = 60.0,
    filename_hint: str | None = None,
    on_attempt: Callable[[str, str, BaseException], None] | None = None,
) -> FetchedImage:
    """按序尝试候选地址，返回首个下载成功的内容；全部失败抛 `ImageFetchError`。"""
    attempts: list[tuple[str, str, str]] = []
    for url in candidates:
        try:
            if not is_usable_url(url):
                raise InvalidImageUrlError(f"非法地址：{url[:120]}")
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
            if not data:
                raise ImageEmptyError("响应内容为空")
            content_type = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
            return FetchedImage(
                data=data,
                filename=pick_filename(url, filename_hint, content_type),
                content_type=content_type or "application/octet-stream",
                url=url,
                attempts=attempts,
            )
        except Exception as e:  # noqa: BLE001 —— 单个候选失败继续下一个
            kind = classify_fetch_error(e)
            attempts.append((url, kind, str(e) or type(e).__name__))
            if on_attempt is not None:
                on_attempt(url, kind, e)
    permanent = bool(attempts) and all(kind == "permanent" for _, kind, _ in attempts)
    raise ImageFetchError(attempts, permanent=permanent)
