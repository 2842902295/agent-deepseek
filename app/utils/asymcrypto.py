"""
非对称加密工具：用于在用户注册 / 登录 / 修改密码链路对密码做传输层加密。

设计要点（详见 CLAUDE.md「鉴权」相关约定与方案 A）：

- RSA-2048 + OAEP(SHA-256) 填充，绝不用裸 RSA 或 PKCS1v1.5。
- 密钥为一次性：前端 `GET /auth/public-key` 取公钥 + keyId，后端把私钥存 Redis
  （TTL 120s，按使用次数扣减），解密后即删，防重放。
- 本模块全部是同步阻塞运算（cryptography 调用），在 async 端点中必须经
  `await asyncio.to_thread(...)` 调用，不得直接调用以免卡死事件循环。

私钥绝不落库，仅短时存于 Redis；argon2 哈希逻辑不受影响——解出明文后照常走
`verify_password` / `get_password_hash`。
"""

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.padding import MGF1, OAEP

# RSA-OAEP with SHA-256；label=None 是标准用法
_OAEP_PADDING = OAEP(mgf=MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)

# Web Crypto 的 `importKey('spki', ...)` 接受 DER 编码的 SubjectPublicKeyInfo
_PUBLIC_FORMAT = serialization.PublicFormat.SubjectPublicKeyInfo
_DER = serialization.Encoding.DER
_PEM = serialization.Encoding.PEM


def generate_keypair() -> tuple[str, str]:
    """生成一次性 RSA-2048 密钥对。

    Returns:
        (private_pem, public_spki_base64)
        - private_pem: PKCS#8 PEM 文本，存 Redis 供后端解密
        - public_spki_base64: SPKI DER 的 base64，直接给前端 Web Crypto importKey
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=_PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_der = private_key.public_key().public_bytes(encoding=_DER, format=_PUBLIC_FORMAT)
    public_b64 = base64.b64encode(public_der).decode("ascii")
    return private_pem, public_b64


def decrypt_password(ciphertext_b64: str, private_pem: str) -> str:
    """用私钥 OAEP 解密前端用公钥加密出的 base64 密文，返回明文密码。

    同步阻塞调用；async 端点请用 `await asyncio.to_thread(decrypt_password, ct, pem)`。
    """
    private_key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    ciphertext = base64.b64decode(ciphertext_b64)
    plaintext = private_key.decrypt(ciphertext, _OAEP_PADDING)
    return plaintext.decode("utf-8")
