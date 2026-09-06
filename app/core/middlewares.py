import re
from datetime import datetime
from uuid import uuid4

import orjson
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.bgtask import BgTasks
from app.core.code import Code
from app.core.ctx import CTX_USER_ID, CTX_X_REQUEST_ID
from app.core.dependency import check_token
from app.core.exceptions import HTTPException
from app.models.system import APILog, Log, LogType, User
from app.settings import APP_SETTINGS


class SimpleBaseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        await self.handle_http(scope, receive, send)

    async def handle_http(self, scope, receive, send) -> None:
        request = Request(scope, receive)
        response = await self.before_request(request) or self.app

        async def send_wrapper(_response):
            await self.after_request(request, _response)
            await send(_response)

        await response(scope, receive, send_wrapper)

    async def before_request(self, request: Request) -> ASGIApp | None:
        ...

    async def after_request(self, request: Request, response: dict):
        ...


class BackGroundTaskMiddleware(SimpleBaseMiddleware):
    async def before_request(self, request: Request) -> ASGIApp | None:
        await BgTasks.init_bg_tasks_obj()
        return self.app

    async def after_request(self, request: Request, response: dict) -> None:
        await BgTasks.execute_tasks()


# 请求 / 响应体超过该尺寸不解析、不整存 api_logs（整板工作流 JSON 这类大载荷否则会让每个请求
# 额外吃一次全量 JSON 解析 + 一次大字段写库，workers=1 的事件循环上直接放大成接口延迟）。
# 超限只记业务码与耗时，审计元信息（路径 / 参数 / 耗时 / 状态码）不受影响。
_LOG_BODY_LIMIT = 64 * 1024
_RESP_CODE_RE = re.compile(rb'"code"\s*:\s*"([^"]{1,6})"')


def _log_skip(path: str) -> bool:
    """高频轻量端点完全跳过 API 日志：每记一条日志要附送 User 查询 + APILog / Log 建、读、写
    约 6 次 DB 操作，workers=1 单事件循环下拥塞时段请求排队超时，轮询类接口首当其冲。
    这类端点是机器高频心跳，无审计价值；鉴权不受影响（AuthControl 独立验 token 并设 CTX_USER_ID）。"""
    return path.endswith("/meta") and "/agent-workflows/" in path


class APILoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.start_time = datetime.now()
        path = request.url.path
        x_request_id = uuid4().hex
        CTX_X_REQUEST_ID.set(x_request_id)
        request.state.x_request_id = x_request_id
        if (
                not _log_skip(path)
                and all([declude not in path for declude in APP_SETTINGS.ADD_LOG_ORIGINS_DECLUDE])
                and (
                "*" in APP_SETTINGS.ADD_LOG_ORIGINS_INCLUDE
                or any([include in path for include in APP_SETTINGS.ADD_LOG_ORIGINS_INCLUDE]))
        ):
            if request.scope["type"] == "http":
                token = request.headers.get("Authorization")
                user_obj = None
                if token:
                    status, _, decode_data = check_token(token.replace("Bearer ", "", 1))
                    if status and decode_data:
                        user_id = int(decode_data["data"]["userId"])
                        user_obj = await User.filter(id=user_id).first()
                        if user_obj:
                            CTX_USER_ID.set(user_id)
                request_data = None
                if request.method in ["POST", "PUT", "PATCH"]:
                    # request.body() 读过的字节会被 starlette 缓存，后续 FastAPI 解析不受影响
                    raw_body = await request.body()
                    if len(raw_body) > _LOG_BODY_LIMIT:
                        request_data = {"_elided": f"request body {len(raw_body)} bytes exceeds {_LOG_BODY_LIMIT}"}
                    elif raw_body:
                        try:
                            request_data = orjson.loads(raw_body)
                        except (orjson.JSONDecodeError, UnicodeDecodeError, ValueError):
                            request_data = None

                url = str(request.url.path)
                if len(url) > 500:
                    raise HTTPException(msg="请求url path过长, 请联系开发人员", code=Code.FAIL)

                api_log_data = dict(
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    request_domain=request.url.hostname,
                    request_path=request.url.path,
                    request_params=dict(request.query_params) or None,
                    request_data=request_data,
                    x_request_id=x_request_id,
                )

                api_log_obj = await APILog.create(**api_log_data)
                request.state.api_log_id = api_log_obj.id
                await Log.create(log_type=LogType.ApiLog, by_user=user_obj, api_log=api_log_obj, x_request_id=x_request_id)

        response = await call_next(request)
        return response


class APILoggerAddResponseMiddleware(SimpleBaseMiddleware):
    """
    需要与APILoggerMiddleware搭配使用
    """

    async def after_request(self, request: Request, response: dict) -> None:
        if response.get("type") == "http.response.body" and hasattr(request.state, "api_log_id"):
            # StreamingResponse / SSE 会发多个 chunk，跳过中间帧只处理最后一帧；
            # 二进制响应（Excel/Word/文件下载）orjson 会抛 JSONDecodeError 被 except 吃掉。
            if response.get("more_body"):
                return
            response_body = response.get("body", b"")
            if not response_body:
                return
            if len(response_body) > _LOG_BODY_LIMIT:
                # 大响应体（整板工作流读等）：不解析不整存，只记业务码与耗时——
                # 标准信封 code 在最前，头部字节里正则即得，省掉全量 orjson.loads 与大字段写库
                try:
                    api_log_obj = await APILog.get(id=request.state.api_log_id)
                    if api_log_obj:
                        m = _RESP_CODE_RE.search(response_body[:512])
                        api_log_obj.response_code = m.group(1).decode() if m else None
                        api_log_obj.process_time = (datetime.now() - request.state.start_time).total_seconds()
                        await api_log_obj.save()
                except Exception:
                    # 写日志失败不影响业务响应
                    ...
                return
            try:
                resp = orjson.loads(response_body)
            except (orjson.JSONDecodeError, UnicodeDecodeError, TypeError):
                return
            # 仅 dict 形态的标准响应（{code,msg,data}）才入库；其他形态（数字/数组/字符串）跳过
            if not isinstance(resp, dict):
                return
            try:
                api_log_obj = await APILog.get(id=request.state.api_log_id)
                if api_log_obj:
                    api_log_obj.response_data = resp
                    api_log_obj.response_code = resp.get("code", "-1")
                    api_log_obj.process_time = (datetime.now() - request.state.start_time).total_seconds()
                    await api_log_obj.save()
            except Exception:
                # 写日志失败不影响业务响应
                ...

        if response.get("type") == "http.response.start" and hasattr(request.state, "x_request_id"):
            response["headers"].append((b"x-request-id", request.state.x_request_id.encode()))
