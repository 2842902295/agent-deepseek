import asyncio
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from fastapi_cache import JsonCoder
from fastapi_cache.decorator import cache

from app.api.v1.utils import insert_log
from app.controllers.user import user_controller
from app.core.code import Code
from app.core.ctx import CTX_USER_ID
from app.core.dependency import DependAuth, check_token
from app.core.redis import AioRedis
from app.log import log
from app.models.system import Button, LogDetailType, LogType, Role, StatusType, User
from app.schemas.base import Fail, Success
from app.schemas.login import CredentialsSchema, JWTOut, JWTPayload, SmsCodeRequest, SmsLoginRequest
from app.schemas.users import UpdatePassword, UserCreate, UserProfileUpdate, UserRegister, validate_password_strength
from app.settings import APP_SETTINGS
from app.utils.asymcrypto import decrypt_password
from app.utils.pwd_crypto import DECRYPT_FAIL_MSG, decrypt_password_field, issue_keypair, acquire_private_key
from app.utils.security import create_access_token, get_password_hash, verify_password
from app.utils.sms import check_verify_code, send_verify_code

router = APIRouter()

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _make_token_pair(user_obj: User) -> JWTOut:
    payload = JWTPayload(
        data={"userId": user_obj.id, "userName": user_obj.user_name, "tokenType": "accessToken"},
        iat=datetime.now(UTC),
        exp=datetime.now(UTC),
    )
    access_payload = payload.model_copy(deep=True)
    access_payload.exp += timedelta(minutes=APP_SETTINGS.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_payload = payload.model_copy(deep=True)
    refresh_payload.data["tokenType"] = "refreshToken"
    refresh_payload.exp += timedelta(minutes=APP_SETTINGS.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    return JWTOut(
        access_token=create_access_token(data=access_payload),
        refresh_token=create_access_token(data=refresh_payload),
    )


@router.get("/public-key", summary="获取一次性密码加密公钥")
async def _(redis: AioRedis, usage: int = 1):
    # usage：该 keyId 允许解密的密码个数（登录/注册=1，修改密码=2，旧新共用）
    usage = max(1, min(3, usage))
    key_id, public_b64 = await issue_keypair(redis, usage)
    return Success(data={"keyId": key_id, "publicKey": public_b64, "alg": "RSA-OAEP-256"})


@router.post("/login", summary="登录")
async def _(credentials: CredentialsSchema, redis: AioRedis):
    # password 为 RSA-OAEP 密文，先解密成明文再走既有 argon2 校验
    plain = await decrypt_password_field(redis, credentials.key_id, credentials.password or "")
    if plain is None:
        return Fail(msg=DECRYPT_FAIL_MSG)
    credentials.password = plain
    user_obj: User = await user_controller.authenticate(credentials)
    await user_controller.update_last_login(user_obj.id)
    data = _make_token_pair(user_obj)
    log.info(f"用户登录成功, 用户名: {user_obj.user_name}")
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserLoginSuccess, by_user_id=user_obj.id)
    return Success(data=data.model_dump(by_alias=True))


@router.post("/register", summary="用户自助注册")
async def _(payload: UserRegister, redis: AioRedis):
    # password 为 RSA-OAEP 密文，解密后做强度校验，再交给 argon2 落库
    plain = await decrypt_password_field(redis, payload.key_id, payload.password)
    if plain is None:
        return Fail(msg=DECRYPT_FAIL_MSG)
    try:
        validate_password_strength(plain)
    except ValueError as e:
        return Fail(msg=str(e))

    # 账号 = 手机号，所以只需校验手机号唯一即可
    if await User.filter(user_phone=payload.user_phone).exists():
        return Fail(msg="该手机号已被注册")
    if await user_controller.get_by_username(payload.user_phone):
        return Fail(msg="该手机号已被占用")
    # R_USER 角色须存在
    role_user = await Role.filter(role_code="R_USER").first()
    if not role_user:
        return Fail(msg="系统未配置默认用户角色，请联系管理员")

    user_create = UserCreate(
        userName=payload.user_phone,
        password=plain,
        userPhone=payload.user_phone,
        nickName=payload.nick_name,
        statusType=StatusType.enable,
    )
    user_obj = await user_controller.create(user_create)
    await user_obj.by_user_roles.add(role_user)

    log.info(f"新用户注册成功, 手机号: {user_obj.user_phone}, 名字: {user_obj.nick_name}")
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserRegisterSuccess, by_user_id=user_obj.id)

    await user_controller.update_last_login(user_obj.id)
    data = _make_token_pair(user_obj)
    return Success(data=data.model_dump(by_alias=True), msg="注册成功")


@router.post("/change-password", summary="修改密码", dependencies=[DependAuth])
async def _(body: UpdatePassword, redis: AioRedis):
    user_id = CTX_USER_ID.get()
    user: User = await user_controller.get(id=user_id)
    if not user.password:
        # 短信登录自动建号的用户无密码，不能走"修改密码"流程
        return Fail(msg="当前账号未设置密码，请使用短信验证码登录")

    # 旧/新密码共用同一个 keyId（usage=2）：一次性扣减两次取私钥，分别解密
    priv = await acquire_private_key(redis, body.key_id, 2)
    if not priv:
        return Fail(msg=DECRYPT_FAIL_MSG)
    try:
        old_plain = await asyncio.to_thread(decrypt_password, body.old_password, priv)
        new_plain = await asyncio.to_thread(decrypt_password, body.new_password, priv)
    except Exception as e:  # noqa: BLE001
        log.warning(f"密码密文解密失败, user_id={user_id}, err={e}")
        return Fail(msg=DECRYPT_FAIL_MSG)

    if not verify_password(old_plain, user.password):
        return Fail(msg="旧密码不正确")
    try:
        validate_password_strength(new_plain)
    except ValueError as e:
        return Fail(msg=str(e))

    user.password = get_password_hash(new_plain)
    await user.save()
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserUpdatePassword, by_user_id=user_id)
    return Success(msg="密码修改成功")


@router.post("/profile", summary="更新个人资料", dependencies=[DependAuth])
async def _(body: UserProfileUpdate):
    user_id = CTX_USER_ID.get()
    user: User = await user_controller.get(id=user_id)

    nick = body.nick_name.strip()
    if not nick:
        return Fail(msg="昵称不能为空")

    email = body.user_email.strip() if body.user_email else ""
    email = email or None
    if email and "@" not in email:
        return Fail(msg="邮箱格式不正确")
    if email and email != user.user_email and await User.filter(user_email=email).exclude(id=user_id).exists():
        return Fail(msg="该邮箱已被占用")

    phone = body.user_phone.strip() if body.user_phone else ""
    if phone:
        if not _PHONE_RE.match(phone):
            return Fail(msg="手机号格式不正确")
        if phone != user.user_phone:
            # 手机号即登录身份：authenticate() 按 user_name / user_phone 两列匹配，两列都要排重
            if await User.filter(user_phone=phone).exclude(id=user_id).exists():
                return Fail(msg="该手机号已被注册")
            if await User.filter(user_name=phone).exclude(id=user_id).exists():
                return Fail(msg="该手机号已被占用")

    user.nick_name = nick
    user.user_gender = body.user_gender
    user.user_email = email
    if phone and phone != user.user_phone:
        # 短信注册用户账号=手机号，换号时同步更新账号
        if user.user_name == user.user_phone:
            user.user_name = phone
        user.user_phone = phone
    await user.save()

    # 注意：GET /user-info 有 60s 缓存，前端须用本响应回填 store
    return Success(
        data={
            "userId": user_id,
            "userName": user.user_name,
            "nickName": user.nick_name,
            "userGender": user.user_gender.value,
            "userEmail": user.user_email,
            "userPhone": user.user_phone,
        },
        msg="更新成功",
    )


@router.post("/sms-code", summary="发送短信验证码（generic 模式）")
async def _(body: SmsCodeRequest, redis: AioRedis):
    phone = body.user_phone
    if not _PHONE_RE.match(phone):
        return Fail(msg="手机号格式不正确")

    try:
        await send_verify_code(phone, redis)
    except Exception as e:
        log.warning(f"短信发送失败, phone={phone[:3]}****, err={e}")
        return Fail(msg=str(e))

    return Success(msg="验证码已发送")


@router.post("/sms-login", summary="短信验证码登录（generic 模式）")
async def _(body: SmsLoginRequest, redis: AioRedis):
    phone = body.user_phone
    if not _PHONE_RE.match(phone):
        return Fail(msg="手机号格式不正确")

    try:
        passed = await check_verify_code(phone, body.code, redis)
    except Exception as e:
        log.error(f"验证码核验异常, phone={phone[:3]}****, err={e}")
        return Fail(msg="验证码核验失败，请稍后重试")

    if not passed:
        return Fail(msg="验证码错误或已过期")

    user_obj = await User.filter(user_phone=phone).first()
    is_new_user = user_obj is None

    if is_new_user:
        if not body.nick_name:
            return Success(data={"isNewUser": True}, msg="新用户，请完善昵称")

        role_user = await Role.filter(role_code="R_USER").first()
        if not role_user:
            return Fail(msg="系统未配置默认用户角色，请联系管理员")

        user_create = UserCreate(
            userName=phone,
            password="",
            userPhone=phone,
            nickName=body.nick_name,
            statusType=StatusType.enable,
        )
        user_obj = await user_controller.create(user_create)
        await user_obj.by_user_roles.add(role_user)
        log.info(f"新用户短信注册, phone={phone[:3]}****, nick={body.nick_name}")
        await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserRegisterSuccess, by_user_id=user_obj.id)
    else:
        if user_obj.status_type == StatusType.disable:
            return Fail(msg="账号已被禁用")

    await user_controller.update_last_login(user_obj.id)
    data = _make_token_pair(user_obj)
    log.info(f"短信登录成功, phone={phone[:3]}****")
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserLoginSuccess, by_user_id=user_obj.id)
    return Success(data={**data.model_dump(by_alias=True), "isNewUser": is_new_user})



@router.post("/refresh-token", summary="刷新认证")
async def _(jwt_token: JWTOut):
    if not jwt_token.refresh_token:
        return Fail(code=Code.INVALID_TOKEN, msg="The refreshToken is not valid.")
    status, code, data = check_token(jwt_token.refresh_token)
    if not status:
        # 刷新接口自身绝不能返回 4010：前端见 4010 会再次触发"刷新并重试"，
        # 而此刻正在等待的就是这个刷新请求，形成循环等待（页面卡死）。
        # refreshToken 过期统一返回 4001，前端据此跳转登录页。
        if code == Code.TOKEN_EXPIRED:
            return Fail(code=Code.INVALID_TOKEN, msg="The refreshToken has expired, please login again.")
        return Fail(code=code, msg=data)

    user_id = data["data"]["userId"]
    user_obj = await user_controller.get(id=user_id)

    if data["data"]["tokenType"] != "refreshToken":
        return Fail(code=Code.INVALID_SESSION, msg="The token is not an refresh token.")

    if user_obj.status_type == StatusType.disable:
        await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserLoginForbid, by_user_id=user_id)
        return Fail(code=Code.ACCOUNT_DISABLED, msg="This user has been disabled.")

    await user_controller.update_last_login(user_id)
    result = _make_token_pair(user_obj)
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserAuthRefreshTokenSuccess, by_user_id=user_obj.id)
    return Success(data=result.model_dump(by_alias=True))


@cache(expire=60, coder=JsonCoder)
@router.get("/user-info", summary="查看用户信息", dependencies=[DependAuth])
async def _():
    user_id = CTX_USER_ID.get()
    user_obj: User = await user_controller.get(id=user_id)
    data = await user_obj.to_dict(exclude_fields=["id", "password", "create_time", "update_time"])

    user_roles: list[Role] = await user_obj.by_user_roles
    user_role_codes = [user_role.role_code for user_role in user_roles]

    user_role_button_codes = [b.button_code for b in await Button.all()] if "R_SUPER" in user_role_codes else [b.button_code for user_role in user_roles for b in await user_role.by_role_buttons]

    user_role_button_codes = list(set(user_role_button_codes))

    data.update({
        "userId": user_id,
        "roles": user_role_codes,
        "buttons": user_role_button_codes
    })
    await insert_log(log_type=LogType.UserLog, log_detail_type=LogDetailType.UserLoginGetUserInfo, by_user_id=user_obj.id)
    return Success(data=data)


@router.get("/error", summary="自定义后端错误")  # todo 使用限流器, 每秒最多一次
async def _(code: str, msg: str):
    if code == Code.TOKEN_EXPIRED:
        return Fail(code=Code.TOKEN_EXPIRED, msg="accessToken已过期")

    return Fail(code=code, msg=f"未知错误, code: {code} msg: {msg}")
