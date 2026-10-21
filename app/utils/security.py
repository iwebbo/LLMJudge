"""SHIM du lab — pas de chiffrement des clés : `decrypt_api_key` est l'identité (même signature)."""


def encrypt_api_key(api_key: str) -> str:
    return api_key


def decrypt_api_key(encrypted_key: str) -> str:
    return encrypted_key
