import json
import random

from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

from app.settings.config import settings

_SMS_CODE_TTL = 300
_SMS_CODE_PREFIX = "sms_code:"


def _sms_key(phone: str) -> str:
    return f"{_SMS_CODE_PREFIX}{phone}"


def _dypns_client():
    from alibabacloud_dypnsapi20170525.client import Client
    config = open_api_models.Config(
        access_key_id=settings.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=settings.ALIYUN_ACCESS_KEY_SECRET,
    )
    config.endpoint = "dypnsapi.aliyuncs.com"
    return Client(config)


def _dysms_client():
    from alibabacloud_dysmsapi20170525.client import Client
    config = open_api_models.Config(
        access_key_id=settings.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=settings.ALIYUN_ACCESS_KEY_SECRET,
    )
    config.endpoint = "dysmsapi.aliyuncs.com"
    return Client(config)


async def send_verify_code(phone: str, redis=None) -> None:
    """
    发送短信验证码。
    dypnsapi：验证码由阿里云生成，redis 不需要。
    dysmsapi：自行生成验证码并存入 redis（须传入 redis 实例）。
    成功时不返回值，失败时抛出 RuntimeError。
    """
    if settings.ALIYUN_SMS_PROVIDER == "dysmsapi":
        if redis is None:
            raise RuntimeError("dysmsapi 模式下 redis 不能为空")
        from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
        code = str(random.randint(100000, 999999))
        await redis.set(_sms_key(phone), code, ex=_SMS_CODE_TTL)
        client = _dysms_client()
        request = dysmsapi_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=settings.ALIYUN_SMS_SIGN_NAME,
            template_code=settings.ALIYUN_SMS_TEMPLATE_CODE,
            template_param=json.dumps({"code": code, "min": str(_SMS_CODE_TTL // 60)}, ensure_ascii=False),
        )
        runtime = util_models.RuntimeOptions()
        resp = await client.send_sms_with_options_async(request, runtime)
        body = resp.body
        if body.code != "OK":
            raise RuntimeError(body.message or "短信发送失败")
    else:
        from alibabacloud_dypnsapi20170525 import models as dypnsapi_models
        client = _dypns_client()
        request = dypnsapi_models.SendSmsVerifyCodeRequest(
            phone_number=phone,
            sign_name=settings.ALIYUN_SMS_SIGN_NAME,
            template_code=settings.ALIYUN_SMS_TEMPLATE_CODE,
            template_param='{"code":"##code##","min":"5"}',
            code_length=6,
            valid_time=_SMS_CODE_TTL,
            code_type=1,
        )
        runtime = util_models.RuntimeOptions()
        resp = await client.send_sms_verify_code_with_options_async(request, runtime)
        body = resp.body.to_map()
        if body.get("Code") != "OK":
            raise RuntimeError(body.get("Message") or "短信发送失败")


async def check_verify_code(phone: str, code: str, redis=None) -> bool:
    """
    核验短信验证码，返回 True 表示通过。
    dypnsapi：调阿里云 CheckSmsVerifyCode 接口核验。
    dysmsapi：从 redis 取出验证码自行比对。
    """
    if settings.ALIYUN_SMS_PROVIDER == "dysmsapi":
        if redis is None:
            return False
        stored = await redis.get(_sms_key(phone))
        if not stored or stored.decode() != code:
            return False
        await redis.delete(_sms_key(phone))
        return True
    else:
        from alibabacloud_dypnsapi20170525 import models as dypnsapi_models
        client = _dypns_client()
        request = dypnsapi_models.CheckSmsVerifyCodeRequest(
            phone_number=phone,
            verify_code=code,
        )
        runtime = util_models.RuntimeOptions()
        resp = await client.check_sms_verify_code_with_options_async(request, runtime)
        body = resp.body.to_map()
        return body.get("Model", {}).get("VerifyResult") == "PASS"
