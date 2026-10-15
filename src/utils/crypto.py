"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : crypto
    Project     : Project-103
    Description : Cryptography and encryption utilities. Handles API key encryption, HMAC signatures, and secure data storage.

    SPDX-License-Identifier: AGPL-3.0

"""

import base64
import hashlib
import hmac
from typing import Optional

from cryptography.fernet import Fernet


class Encryptor:
    """Encrypts/decrypts data"""

    def __init__(self, key: Optional[bytes] = None):
        """
        Initialize the encryptor

        Args:
            key: Encryption key (32 bytes, base64). If not given, a new one is generated
        """
        if key is None:
            key = Fernet.generate_key()
        self.cipher = Fernet(key)
        self.key = key

    def encrypt(self, data: str) -> str:
        """
        Encrypt a string

        Args:
            data: Data to encrypt

        Returns:
            Encrypted data as a string
        """
        encrypted = self.cipher.encrypt(data.encode())
        return encrypted.decode()

    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt a string

        Args:
            encrypted_data: Encrypted data

        Returns:
            Decrypted data
        """
        decrypted = self.cipher.decrypt(encrypted_data.encode())
        return decrypted.decode()

    @staticmethod
    def generate_key() -> bytes:
        """Generate a new encryption key"""
        return Fernet.generate_key()


# Global instance for encrypting API keys
_encryptor: Optional[Encryptor] = None


def init_encryptor(key: Optional[bytes] = None) -> None:
    """
    Initialize the global encryptor

    Args:
        key: Encryption key. If None, a new one is generated
    """
    global _encryptor
    _encryptor = Encryptor(key)


def get_encryptor() -> Encryptor:
    """Get the global encryptor"""
    global _encryptor
    if _encryptor is None:
        init_encryptor()
    return _encryptor


def encrypt_api_key(api_key: str) -> str:
    """
    Encrypt an API key

    Args:
        api_key: API key to encrypt

    Returns:
        Encrypted API key

    Example:
        >>> init_encryptor()
        >>> encrypted = encrypt_api_key("my_secret_key")
        >>> print(encrypted)
        'gAAAAABh...'
    """
    return get_encryptor().encrypt(api_key)


def decrypt_api_key(encrypted_key: str) -> str:
    """
    Decrypt an API key

    Args:
        encrypted_key: Encrypted API key

    Returns:
        Decrypted API key

    Example:
        >>> init_encryptor()
        >>> encrypted = encrypt_api_key("my_secret_key")
        >>> decrypted = decrypt_api_key(encrypted)
        >>> print(decrypted)
        'my_secret_key'
    """
    return get_encryptor().decrypt(encrypted_key)


def hash_string(data: str, algorithm: str = "sha256") -> str:
    """
    Hash a string

    Args:
        data: Data to hash
        algorithm: Hash algorithm (sha256, sha512, md5)

    Returns:
        Hash as a hex string

    Example:
        >>> hash_string("hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    if algorithm == "sha256":
        return hashlib.sha256(data.encode()).hexdigest()
    elif algorithm == "sha512":
        return hashlib.sha512(data.encode()).hexdigest()
    elif algorithm == "md5":
        return hashlib.md5(data.encode()).hexdigest()
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")


def generate_signature(secret: str, message: str, algorithm: str = "sha256") -> str:
    """
    Generate an HMAC signature for a message

    Args:
        secret: Secret key
        message: Message to sign
        algorithm: Hash algorithm

    Returns:
        HMAC signature as a hex string

    Example:
        >>> signature = generate_signature("secret", "message")
        >>> print(signature)
        '8b5f48702995c1598c573db1e21866a9b825d4a794d169d7060a03605796360b'
    """
    if algorithm == "sha256":
        hash_func = hashlib.sha256
    elif algorithm == "sha512":
        hash_func = hashlib.sha512
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")

    signature = hmac.new(secret.encode(), message.encode(), hash_func).hexdigest()

    return signature


def generate_api_signature(
    api_secret: str, timestamp: int, method: str, endpoint: str, params: str = ""
) -> str:
    """
    Generate a signature for an API request (Bybit format)

    Args:
        api_secret: API secret
        timestamp: Unix timestamp in milliseconds
        method: HTTP method (GET, POST)
        endpoint: API endpoint
        params: Query parameters

    Returns:
        HMAC signature

    Example:
        >>> signature = generate_api_signature(
        ...     "secret",
        ...     1234567890,
        ...     "GET",
        ...     "/v5/market/tickers",
        ...     "category=spot&symbol=BTCUSDT"
        ... )
    """
    message = f"{timestamp}{method}{endpoint}{params}"
    return generate_signature(api_secret, message, "sha256")


def verify_signature(secret: str, message: str, signature: str, algorithm: str = "sha256") -> bool:
    """
    Verify an HMAC signature

    Args:
        secret: Secret key
        message: Message
        signature: Signature to verify
        algorithm: Hash algorithm

    Returns:
        True if the signature is valid, else False

    Example:
        >>> signature = generate_signature("secret", "message")
        >>> verify_signature("secret", "message", signature)
        True
        >>> verify_signature("wrong_secret", "message", signature)
        False
    """
    expected_signature = generate_signature(secret, message, algorithm)
    return hmac.compare_digest(signature, expected_signature)


def encode_base64(data: str) -> str:
    """
    Encode a string to base64

    Args:
        data: Data to encode

    Returns:
        Base64-encoded string
    """
    return base64.b64encode(data.encode()).decode()


def decode_base64(encoded_data: str) -> str:
    """
    Decode a string from base64

    Args:
        encoded_data: Base64-encoded data

    Returns:
        Decoded string
    """
    return base64.b64decode(encoded_data.encode()).decode()
