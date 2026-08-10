import re
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.system import GenderType, StatusType

# 密码强度：6-18 位，需同时包含字母、数字和特殊字符（前后端统一）
PASSWORD_PATTERN = r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^\w\s])[^\s]{6,18}$"


def validate_password_strength(v: str | None) -> str | None:
    """对明文密码做强度校验；空值跳过（短信登录自动建号等无密码场景）。

    供端点在 RSA 解密拿到明文后调用——密文阶段不校验。
    """
    if not v:
        return v
    if not re.fullmatch(PASSWORD_PATTERN, v):
        raise ValueError("密码为 6-18 位，需同时包含字母、数字和特殊字符")
    return v


class UserBase(BaseModel):
    user_name: Annotated[str | None, Field(alias="userName", title="用户名")] = None
    # 密码字段为 RSA-OAEP 密文（base64）；后端端点解密后做强度校验。空值表示不改/不设密码
    password: Annotated[str | None, Field(title="密码密文")] = None
    # 一次性密钥 ID；password 非空时必填，后端凭此定位私钥解密
    key_id: Annotated[str | None, Field(alias="keyId", title="一次性密钥 ID")] = None

    user_email: Annotated[str | None, Field(alias="userEmail", title="邮箱")] = None
    user_gender: Annotated[GenderType | None, Field(alias="userGender", title="性别")] = None
    nick_name: Annotated[str | None, Field(alias="nickName", title="昵称")] = None
    user_phone: Annotated[str | None, Field(alias="userPhone", title="手机号")] = None
    status_type: Annotated[StatusType | None, Field(alias="statusType", title="用户状态")] = None

    by_user_role_code_list: Annotated[list[str] | None, Field(alias="byUserRoleCodeList", title="用户角色编码列表")] = None

    class Config:
        populate_by_name = True


class UserSearch(UserBase):
    current: Annotated[int | None, Field(description="页码")] = 1
    size: Annotated[int | None, Field(description="每页数量")] = 10


class UserCreate(UserBase):
    ...


class UserUpdate(UserBase):
    ...


class UpdatePassword(BaseModel):
    # 密码字段均为 RSA-OAEP 密文（base64），后端解密后做强度校验
    old_password: Annotated[str, Field(alias="oldPassword", title="旧密码密文")]
    new_password: Annotated[str, Field(alias="newPassword", title="新密码密文")]
    key_id: Annotated[str, Field(alias="keyId", title="一次性密钥 ID（旧/新共用）")]

    class Config:
        allow_extra = True
        populate_by_name = True


class UserRegister(BaseModel):
    # 密码字段为 RSA-OAEP 密文（base64），后端解密后做强度校验
    password: Annotated[str, Field(title="密码密文")]
    key_id: Annotated[str, Field(alias="keyId", title="一次性密钥 ID")]
    user_phone: Annotated[str, Field(alias="userPhone", min_length=5, max_length=20, title="手机号（同时作为账号）")]
    nick_name: Annotated[str, Field(alias="nickName", min_length=1, max_length=50, title="名字")]

    class Config:
        populate_by_name = True


class UserProfileUpdate(BaseModel):
    """用户自助更新个人资料的轻量入参（昵称/性别/邮箱/手机号），不含角色与密码"""

    nick_name: Annotated[str, Field(alias="nickName", min_length=1, max_length=30, title="昵称")]
    user_gender: Annotated[GenderType, Field(alias="userGender", title="性别")]
    user_email: Annotated[str | None, Field(alias="userEmail", max_length=255, title="邮箱")] = None
    # 空串/缺省视为不修改手机号（不允许在此清空）
    user_phone: Annotated[str | None, Field(alias="userPhone", title="手机号")] = None

    class Config:
        populate_by_name = True


__all__ = [
    "UserBase",
    "UserSearch",
    "UserCreate",
    "UserUpdate",
    "UpdatePassword",
    "UserRegister",
    "UserProfileUpdate",
    "PASSWORD_PATTERN",
    "validate_password_strength",
]
