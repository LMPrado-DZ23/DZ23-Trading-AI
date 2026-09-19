"""Pacote portátil CIFRADO para migração de servidor (§2).

O export do inquilino inclui SEGREDOS (chaves de IA/corretora). Ele nunca sai
como JSON comum: é cifrado com AES-256-GCM, chave derivada de uma FRASE-SENHA
de export por scrypt (parâmetros persistidos no envelope), com AAD ligando o
pacote ao tenant/usuário/propósito/timestamp. Sem a frase, o arquivo é ruído;
adulterar o pacote OU trocar o tenant no AAD invalida a autenticação AEAD.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

MAGIC = "dz23.mudanca.enc/2"
# Parâmetros scrypt PERSISTIDOS no envelope (evolui sem quebrar pacotes antigos).
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode())


def _canonical(aad: dict[str, Any]) -> bytes:
    """AAD canônico e estável (chaves ordenadas) — o mesmo dos dois lados."""
    return json.dumps(aad, sort_keys=True, separators=(",", ":")).encode()


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)


def encrypt_package(data: dict[str, Any], passphrase: str, *,
                    aad: dict[str, Any]) -> dict[str, Any]:
    """Cifra `data` e devolve um envelope versionado, sem nada em claro além do
    AAD (que é público por desenho e AUTENTICADO). Requer frase não vazia."""
    if not passphrase:
        raise ValueError("frase-senha de export vazia: recuse antes de cifrar")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _key(passphrase, salt, _N, _R, _P)
    plaintext = json.dumps(data, ensure_ascii=False, default=str).encode()
    ct = AESGCM(key).encrypt(nonce, plaintext, _canonical(aad))
    return {"formato": MAGIC, "kdf": "scrypt", "n": _N, "r": _R, "p": _P,
            "salt": _b64e(salt), "nonce": _b64e(nonce), "aad": aad,
            "ciphertext": _b64e(ct)}


def decrypt_package(envelope: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """Decifra um envelope. Frase errada, pacote adulterado ou AAD trocado =>
    a autenticação AEAD falha e isto LEVANTA (fail-closed)."""
    if envelope.get("formato") != MAGIC:
        raise ValueError("envelope não é do formato cifrado dz23/2")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = _b64d(envelope["salt"])
    nonce = _b64d(envelope["nonce"])
    ct = _b64d(envelope["ciphertext"])
    key = _key(passphrase, salt, int(envelope["n"]), int(envelope["r"]), int(envelope["p"]))
    aad = envelope.get("aad") or {}
    plaintext = AESGCM(key).decrypt(nonce, ct, _canonical(aad))
    result: dict[str, Any] = json.loads(plaintext.decode())
    return result


def envelope_aad(envelope: dict[str, Any]) -> dict[str, Any]:
    """AAD declarado no envelope (tenant/user/purpose/ts) — para o verificador
    conferir contra o request ANTES de confiar no conteúdo."""
    aad = envelope.get("aad")
    return dict(aad) if isinstance(aad, dict) else {}


__all__ = ["MAGIC", "encrypt_package", "decrypt_package", "envelope_aad"]
