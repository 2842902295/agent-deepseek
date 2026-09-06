"""
一次性 RSA 密钥的 Redis 管理与密码密文解密（共享层）。

被 `app/api/v1/auth/auth.py`（登录/注册/改密）与 `app/api/v1/system_manage/users.py`
（管理员建/改用户）共用，避免跨路由导入。

机制见 CLAUDE.md「模型配置与超管全局切换」同级的密码加密约定：
- 前端 `GET /auth/public-key?usage=N` 取公钥+keyId；私钥入 Redis，按 N 次扣减，用完即删。
- 端点凭 keyId 取私钥 → OAEP 解密 → 明文密码交给 argon2。
- 私钥不落库，仅短存 Redis（TTL + 一次性）。
"""

import asyncio
import secrets

from app.core.redis import AioRedis
from app.log import log
from app.utils.asymcrypto import decrypt_password, generate_keypair

# 一次性密码加密密钥在 Redis 中的前缀与存活时长
_PUBKEY_PREFIX = "pwdkey:"
_PUBKEY_TTL = 120  # 私钥最多存活 120s，且按使用次数扣减，用完即删

# 解密失败的统一提示（不碰鉴权码，走 4000）
DECRYPT_FAIL_MSG = "密码已过期或解密失败，请刷新后重试"


async def issue_keypair(redis: AioRedis, usage: int) -> tuple[str, str]:
    """生成一次性 RSA 密钥对，私钥入 Redis（按 usage 次扣减），返回 (keyId, 公钥 base64)。"""
    private_pem, public_b64 = await asyncio.to_thread(generate_keypair)
    key_id = secrets.token_urlsafe(16)
    key = _PUBKEY_PREFIX + key_id
    async with redis.pipeline() as pipe:
        pipe.hset(key, mapping={"priv": private_pem, "remaining": usage})
        pipe.expire(key, _PUBKEY_TTL)
        await pipe.execute()
    return key_id, public_b64


async def acquire_private_key(redis: AioRedis, key_id: str | None, count: int) -> str | None:
    """按 count 扣减一次性密钥可用次数并返回私钥 PEM；不可用（过期/次数不足）返回 None。"""
    if not key_id:
        return None
    key = _PUBKEY_PREFIX + key_id
    raw = await redis.hgetall(key)
    if not raw:
        return None
    # 项目 redis 未开 decode_responses，key/value 均为 bytes，这里统一解码
    data = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    remaining = int(data.get("remaining", "0"))
    if remaining < count:
        await redis.delete(key)
        return None
    new_remaining = await redis.hincrby(key, "remaining", -count)
    if new_remaining <= 0:
        await redis.delete(key)
    return data.get("priv")


async def decrypt_password_field(redis: AioRedis, key_id: str | None, ciphertext: str, count: int = 1) -> str | None:
    """取私钥 + OAEP 解密单个密码密文。失败（密钥过期/密文非法）统一返回 None。"""
    priv = await acquire_private_key(redis, key_id, count)
    if not priv:
        return None
    try:
        return await asyncio.to_thread(decrypt_password, ciphertext, priv)
    except Exception as e:  # noqa: BLE001
        log.warning(f"密码密文解密失败, err={e}")
        return None
