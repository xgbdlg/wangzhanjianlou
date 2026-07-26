# security.py
# 加密存储相关工具：PBKDF2 密钥派生与 Fernet 对称加密

import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 这里示例使用固定盐，实际部署请改为安全来源并妥善存储
SALT = b"csgoempire-auction-salt-2026"
ITERATIONS = 390000


def derive_key(password: str) -> bytes:
    """从口令派生 Fernet 密钥。"""
    password_bytes = password.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SALT,
        iterations=ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password_bytes))
    return key


def encrypt_data(secret: str, password: str) -> str:
    """加密文本并返回 URL-safe Base64 字符串。"""
    key = derive_key(password)
    f = Fernet(key)
    token = f.encrypt(secret.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_data(token: str, password: str) -> str:
    """解密文本并返回明文字符串。"""
    key = derive_key(password)
    f = Fernet(key)
    decrypted = f.decrypt(token.encode("utf-8"))
    return decrypted.decode("utf-8")
