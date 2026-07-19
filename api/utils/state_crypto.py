#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import base64
import gzip
import hashlib
import json
import logging
import os
from typing import Any, Dict, Iterable, Optional

from agent.component.state_fields import (
    SENSITIVE_STATE_FIELDS,
    STATE_SCHEMA_VERSION,
    clamp_state_size,
    upgrade_state,
)

"""Agent 状态加密与序列化工具模块。

对应需求：P2-FR-01（Agent 状态标准化 - 安全加密）

功能说明：
  提供 Agent 状态的加密/解密和序列化/反序列化能力，确保状态在 Redis 持久化时
  敏感字段被加密保护，同时支持高效的压缩存储。

实现方式：
  1. 密钥管理：从环境变量 RAGFLOW_SECRET_KEY 或 settings.SECRET_KEY 获取原始密钥，
     通过 SHA-256 派生 32 字节 AES 密钥。未配置时使用开发默认密钥并输出警告。
  2. AES 加密：使用 AES-CBC 模式 + PKCS7 填充 + 随机 IV，对敏感字段（messages、
     retrieved_docs、final_answer、graded_docs）进行独立加密，密文 base64 编码存储。
  3. 序列化格式：
     - 运行时（Redis 缓存）：JSON 格式，便于调试和跨语言读取
     - 持久化（检查点）：MessagePack + gzip 压缩，体积更小，序列化更快
  4. secure_serialize/deserialize 系列函数：组合加密 + 序列化 + schema 升级，
     提供一站式的安全序列化接口。
  5. 依赖兼容：AES 依赖 pycryptodomex（可选），MessagePack 优先使用 ormsgpack，
     回退到 msgpack，再回退到 JSON。
"""

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import pad, unpad

    _HAS_AES = True
except Exception:
    _HAS_AES = False
    logging.warning("pycryptodomex AES is not available; state encryption is disabled")

# Prefer the project's ormsgpack dependency, fall back to plain JSON if absent.
try:
    import ormsgpack as _msgpack

    _HAS_MSGPACK = True
except Exception:
    try:
        import msgpack as _msgpack

        _HAS_MSGPACK = True
    except Exception:
        _HAS_MSGPACK = False


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_STATE_KEY_ENV = "RAGFLOW_SECRET_KEY"
_STATE_DEFAULT_KEY = "ragflow-state-default-key"


def _get_raw_secret_key() -> str:
    """获取用于派生 AES 密钥的原始密钥字符串。

    优先级：RAGFLOW_SECRET_KEY 环境变量 > settings.SECRET_KEY > 开发默认密钥。
    生产环境必须配置 RAGFLOW_SECRET_KEY，否则使用默认密钥会输出警告日志。
    """
    key = os.environ.get(_STATE_KEY_ENV)
    if key:
        return key

    try:
        from common import settings

        key = getattr(settings, "SECRET_KEY", None)
    except Exception:
        key = None

    if key:
        return key

    logging.warning(
        "STATE_CRYPTO: %s is not configured and SECRET_KEY is unavailable; "
        "using the default development key. Set %s in production.",
        _STATE_KEY_ENV, _STATE_KEY_ENV,
    )
    return _STATE_DEFAULT_KEY


def _derive_aes_key(raw_key: Optional[str] = None) -> bytes:
    """从原始密钥字符串派生 32 字节 AES-256 密钥。

    使用 SHA-256 哈希确保密钥长度固定为 32 字节，同时提供均匀的密钥分布。
    """
    raw = raw_key if raw_key is not None else _get_raw_secret_key()
    return hashlib.sha256(raw.encode("utf-8")).digest()


# ---------------------------------------------------------------------------
# AES encryption / decryption for individual values
# ---------------------------------------------------------------------------

def encrypt_value(value: Any, raw_key: Optional[str] = None) -> str:
    """使用 AES-CBC 模式加密单个值。

    加密流程：
      1. 将输入值转为 UTF-8 字节（字符串直接编码，其他类型先 JSON 序列化）
      2. 生成 16 字节随机 IV（每次加密使用不同 IV，确保相同明文产生不同密文）
      3. AES-CBC 加密 + PKCS7 填充
      4. 将 IV + 密文拼接后 base64 编码返回

    返回格式：base64(IV + ciphertext)，解密时前 16 字节为 IV。
    """
    if not _HAS_AES:
        raise RuntimeError("pycryptodomex AES is not available")

    if isinstance(value, str):
        plaintext = value.encode("utf-8")
    else:
        plaintext = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")

    iv = os.urandom(16)
    cipher = AES.new(_derive_aes_key(raw_key), AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def decrypt_value(ciphertext: str, raw_key: Optional[str] = None) -> Any:
    """Decrypt a value produced by :func:`encrypt_value`."""
    if not _HAS_AES:
        raise RuntimeError("pycryptodomex AES is not available")

    raw = base64.b64decode(ciphertext.encode("utf-8"))
    iv, encrypted = raw[:16], raw[16:]
    cipher = AES.new(_derive_aes_key(raw_key), AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(encrypted), AES.block_size).decode("utf-8")

    try:
        return json.loads(plaintext)
    except Exception:
        return plaintext


# ---------------------------------------------------------------------------
# State-level encryption helpers
# ---------------------------------------------------------------------------

def encrypt_sensitive_fields(
    state: Dict[str, Any],
    fields: Iterable[str] = SENSITIVE_STATE_FIELDS,
    raw_key: Optional[str] = None,
) -> Dict[str, Any]:
    """对状态字典中配置的敏感字段进行 AES 加密。

    遍历 SENSITIVE_STATE_FIELDS 中列出的字段，将其值加密为 base64 字符串。
    加密失败的字段保持原值并输出警告日志，不影响其他字段的加密。
    如果 AES 库不可用，返回原始状态的浅拷贝（降级为不加密）。
    """
    if not _HAS_AES:
        return dict(state)

    encrypted = dict(state)
    for field in fields:
        if field not in encrypted or encrypted[field] is None:
            continue
        try:
            encrypted[field] = encrypt_value(encrypted[field], raw_key)
        except Exception as e:
            logging.warning("Failed to encrypt state field '%s': %s", field, e)
    return encrypted


def decrypt_sensitive_fields(
    state: Dict[str, Any],
    fields: Iterable[str] = SENSITIVE_STATE_FIELDS,
    raw_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Decrypt fields previously encrypted by :func:`encrypt_sensitive_fields`."""
    if not _HAS_AES:
        return dict(state)

    decrypted = dict(state)
    for field in fields:
        value = decrypted.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            continue
        try:
            decrypted[field] = decrypt_value(value, raw_key)
        except Exception as e:
            logging.debug("State field '%s' does not appear encrypted: %s", field, e)
    return decrypted


# ---------------------------------------------------------------------------
# Serialization formats
# ---------------------------------------------------------------------------

def serialize_runtime_state(
    state: Dict[str, Any],
    max_size_mb: Optional[float] = None,
) -> str:
    """将状态序列化为 JSON 字符串，用于运行时/Redis 缓存场景。

    流程：先执行大小压缩（clamp_state_size），再 JSON 序列化。
    JSON 格式便于调试和跨语言读取，但不适合大量数据的持久化存储。
    """
    state = clamp_state_size(state, max_size_mb=max_size_mb)
    return json.dumps(state, ensure_ascii=False, default=str)


def deserialize_runtime_state(payload: str) -> Dict[str, Any]:
    """Deserialize a runtime JSON state payload and upgrade it."""
    state = json.loads(payload)
    state = upgrade_state(state)
    if state.get("state_schema_version") != STATE_SCHEMA_VERSION:
        state["state_schema_version"] = STATE_SCHEMA_VERSION
    return state


def serialize_persistent_state(
    state: Dict[str, Any],
    max_size_mb: Optional[float] = None,
) -> str:
    """将状态序列化为 MessagePack+gzip 压缩格式，用于检查点持久化存储。

    流程：先执行大小压缩 -> MessagePack 二进制序列化 -> gzip 压缩 -> base64 编码。
    相比 JSON，MessagePack 体积更小（通常减少 30-50%），序列化/反序列化速度更快。
    如果 MessagePack 库不可用，回退到 JSON+gzip。
    """
    state = clamp_state_size(state, max_size_mb=max_size_mb)

    if _HAS_MSGPACK:
        data: bytes = _msgpack.packb(state)
    else:
        data = json.dumps(state, ensure_ascii=False, default=str).encode("utf-8")

    compressed = gzip.compress(data, compresslevel=6)
    return base64.b64encode(compressed).decode("utf-8")


def deserialize_persistent_state(payload: str) -> Dict[str, Any]:
    """Deserialize a persistent state payload and upgrade it."""
    raw = base64.b64decode(payload.encode("utf-8"))
    data = gzip.decompress(raw)

    if _HAS_MSGPACK:
        state = _msgpack.unpackb(data)
    else:
        state = json.loads(data.decode("utf-8"))

    state = upgrade_state(state)
    if state.get("state_schema_version") != STATE_SCHEMA_VERSION:
        state["state_schema_version"] = STATE_SCHEMA_VERSION
    return state


# ---------------------------------------------------------------------------
# Convenience helpers for encrypted persistence
# ---------------------------------------------------------------------------

def secure_serialize_runtime(
    state: Dict[str, Any],
    encrypt: bool = True,
    max_size_mb: Optional[float] = None,
    raw_key: Optional[str] = None,
) -> str:
    """Serialize runtime state, optionally encrypting sensitive fields."""
    if encrypt and _HAS_AES:
        state = encrypt_sensitive_fields(state, raw_key=raw_key)
    return serialize_runtime_state(state, max_size_mb=max_size_mb)


def secure_deserialize_runtime(
    payload: str,
    encrypted: bool = True,
    raw_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Deserialize runtime state, optionally decrypting sensitive fields."""
    state = json.loads(payload)
    if encrypted and _HAS_AES:
        state = decrypt_sensitive_fields(state, raw_key=raw_key)
    state = upgrade_state(state)
    if state.get("state_schema_version") != STATE_SCHEMA_VERSION:
        state["state_schema_version"] = STATE_SCHEMA_VERSION
    return state


def secure_serialize_persistent(
    state: Dict[str, Any],
    encrypt: bool = True,
    max_size_mb: Optional[float] = None,
    raw_key: Optional[str] = None,
) -> str:
    """安全持久化序列化：先加密敏感字段，再执行 MessagePack+gzip 压缩。

    这是检查点存储的标准序列化方式，确保：
      1. 敏感字段（对话、文档等）被 AES 加密保护
      2. 整体数据经过高效压缩，减少 Redis 内存占用
      3. 输出为 base64 字符串，可安全存储在任何文本存储中
    """
    if encrypt and _HAS_AES:
        state = encrypt_sensitive_fields(state, raw_key=raw_key)
    return serialize_persistent_state(state, max_size_mb=max_size_mb)


def secure_deserialize_persistent(
    payload: str,
    encrypted: bool = True,
    raw_key: Optional[str] = None,
) -> Dict[str, Any]:
    """安全持久化反序列化：先解压 -> 解密敏感字段 -> 升级 schema 版本。

    这是检查点加载的标准反序列化方式。注意操作顺序：
      1. base64 解码 -> gzip 解压 -> MessagePack/JSON 反序列化
      2. 解密敏感字段（必须在 schema 升级之前，否则加密的列表字段会被错误处理）
      3. 执行 schema 升级，填充新版本中新增的字段默认值
    """
    raw = base64.b64decode(payload.encode("utf-8"))
    data = gzip.decompress(raw)

    if _HAS_MSGPACK:
        state = _msgpack.unpackb(data)
    else:
        state = json.loads(data.decode("utf-8"))

    if encrypted and _HAS_AES:
        state = decrypt_sensitive_fields(state, raw_key=raw_key)
    state = upgrade_state(state)
    if state.get("state_schema_version") != STATE_SCHEMA_VERSION:
        state["state_schema_version"] = STATE_SCHEMA_VERSION
    return state
