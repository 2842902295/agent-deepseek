from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.schemas.login import JWTPayload
from app.settings import APP_SETTINGS

# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# ALGORITHM = "HS256"


def create_access_token(*, data: JWTPayload):
    payload = data.model_dump().copy()
    encoded_jwt = jwt.encode(payload, APP_SETTINGS.SECRET_KEY, algorithm=APP_SETTINGS.JWT_ALGORITHM)
    return encoded_jwt


def create_html_app_token(user_id: int, workflow_key: str, ttl_days: int = 7, share: bool = False, visitor_id: int | None = None) -> str:
    """签发应用制作的托管 token（无状态，URL 内嵌，供 iframe 及其相对引用资源使用）。

    tokenType 用独立的 "htmlAppToken"：鉴权依赖（dependency.py）硬性校验 tokenType == "accessToken"，
    该 token 打到任何登录态接口都会被拒；反向托管路由只认 htmlAppToken——双向天然隔离，零改动。
    权限域 = 单个任务目录（users/{uid}/apps/{workflow_key}/）的读 + json 写回。
    share=True 为访客分享态：看板开启分享后访客拿到的 token——数据写回 / 页内 AI 照常，
    但 serve 时不注入「编辑文字」脚本（不允许编辑看板本身）。
    visitor_id：仅「仅登录用户」分享模式签发时传入访客用户ID（claims 带 visitorId，
    行数据通道与 whoami 据此识别访客身份）；免登录公开分享与板主 token 均不带。
    """
    claims: dict[str, Any] = {"tokenType": "htmlAppToken", "userId": user_id, "workflowKey": workflow_key}
    if share:
        claims["share"] = True
    if visitor_id is not None:
        claims["visitorId"] = visitor_id
    payload = JWTPayload(
        data=claims,
        iat=datetime.now(UTC),
        exp=datetime.now(UTC),
    )
    payload.exp += timedelta(days=ttl_days)
    return create_access_token(data=payload)


def decode_html_app_token(token: str) -> dict | None:
    """校验应用制作托管 token；任何异常 / tokenType 不符 / 缺 claims 一律返回 None（公开路由静默 404）。

    返回 {"userId", "workflowKey", "share", "visitorId"}：share=True 表示访客分享态 token；
    visitorId 仅「仅登录用户」分享态携带（旧格式 / 免登录 / 板主 token 均为 None）。"""
    try:
        options = {"verify_signature": True, "verify_aud": False, "exp": True}
        decoded: dict[str, Any] = jwt.decode(token, APP_SETTINGS.SECRET_KEY, algorithms=[APP_SETTINGS.JWT_ALGORITHM], options=options)
    except Exception:
        return None
    data = decoded.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("tokenType") != "htmlAppToken":
        return None
    uid = data.get("userId")
    wk = data.get("workflowKey")
    if not isinstance(uid, int) or not isinstance(wk, str) or not wk:
        return None
    vid = data.get("visitorId")
    return {"userId": uid, "workflowKey": wk, "share": bool(data.get("share")), "visitorId": vid if isinstance(vid, int) else None}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)



