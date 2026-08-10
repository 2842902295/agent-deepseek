from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class CredentialsSchema(BaseModel):
    user_name: Annotated[str | None, Field(alias="userName", title="用户名")]
    # 密码字段为 RSA-OAEP 密文（base64），后端解密后再喂给 verify_password
    password: Annotated[str | None, Field(title="密码密文")]
    key_id: Annotated[str | None, Field(alias="keyId", title="一次性密钥 ID")] = None

    class Config:
        allow_extra = True
        populate_by_name = True


class JWTOut(BaseModel):
    access_token: Annotated[str | None, Field(alias="token", title="请求token")] = None
    refresh_token: Annotated[str | None, Field(alias="refreshToken", title="刷新token")] = None

    class Config:
        allow_extra = True
        populate_by_name = True


class JWTPayload(BaseModel):
    data: dict
    iat: datetime
    exp: datetime

    class Config:
        allow_extra = True
        populate_by_name = True


class SmsCodeRequest(BaseModel):
    user_phone: Annotated[str, Field(alias="userPhone", min_length=11, max_length=11, title="手机号")]

    class Config:
        populate_by_name = True


class SmsLoginRequest(BaseModel):
    user_phone: Annotated[str, Field(alias="userPhone", min_length=11, max_length=11, title="手机号")]
    code: Annotated[str, Field(min_length=4, max_length=8, title="验证码")]
    nick_name: Annotated[str | None, Field(alias="nickName", min_length=1, max_length=50, title="昵称（新用户必填）")] = None

    class Config:
        populate_by_name = True


__all__ = ["CredentialsSchema", "JWTOut", "JWTPayload", "SmsCodeRequest", "SmsLoginRequest"]
