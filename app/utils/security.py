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


def create_html_app_token(user_id: int, workflow_key: str, ttl_days: int = 7) -> str:
    """签发 HTML 看板应用的托管 token（无状态，URL 内嵌，供 iframe 及其相对引用资源使用）。

    tokenType 用独立的 "htmlAppToken"：鉴权依赖（dependency.py）硬性校验 tokenType == "accessToken"，
    该 token 打到任何登录态接口都会被拒；反向托管路由只认 htmlAppToken——双向天然隔离，零改动。
    权限域 = 单个任务目录（users/{uid}/apps/{workflow_key}/）的读 + json 写回。
    """
    payload = JWTPayload(
        data={"tokenType": "htmlAppToken", "userId": user_id, "workflowKey": workflow_key},
        iat=datetime.now(UTC),
        exp=datetime.now(UTC),
    )
    payload.exp += timedelta(days=ttl_days)
    return create_access_token(data=payload)


def decode_html_app_token(token: str) -> dict | None:
    """校验 HTML 看板托管 token；任何异常 / tokenType 不符 / 缺 claims 一律返回 None（公开路由静默 404）。"""
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
    return {"userId": uid, "workflowKey": wk}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)



