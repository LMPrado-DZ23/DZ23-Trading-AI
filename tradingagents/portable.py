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
# Limites anti-DoS (§4): KDF por allowlist, parâmetros e tamanhos capados ANTES
# de tocar scrypt/decode. Frase forte obrigatória.
_KDF_ALLOW = frozenset({"scrypt"})
_N_MAX, _R_MAX, _P_MAX = 2 ** 20, 32, 16
_SALT_LEN, _NONCE_LEN = 16, 12
_CIPHERTEXT_MAX = 8 * 1024 * 1024          # 8 MiB de dados cifrados
MIN_PASSPHRASE = 12


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: Any) -> bytes:
    # base64 ESTRITO: rejeita lixo/caracteres fora do alfabeto e valores ausentes.
    if not isinstance(s, str):
        raise ValueError("campo base64 ausente ou não-string")
    return base64.b64decode(s.encode(), validate=True)


def _canonical(aad: dict[str, Any]) -> bytes:
    """AAD canônico e estável (chaves ordenadas) — o mesmo dos dois lados."""
    return json.dumps(aad, sort_keys=True, separators=(",", ":")).encode()


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)


def encrypt_package(data: dict[str, Any], passphrase: str, *,
                    aad: dict[str, Any]) -> dict[str, Any]:
    """Cifra `data` e devolve um envelope versionado, sem nada em claro além do
    AAD (que é público por desenho e AUTENTICADO). Frase FORTE obrigatória."""
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise ValueError(f"frase-senha de export fraca (mínimo {MIN_PASSPHRASE} caracteres)")
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
    if not isinstance(envelope, dict) or envelope.get("formato") != MAGIC:
        raise ValueError("envelope não é do formato cifrado dz23/2")
    if envelope.get("kdf") not in _KDF_ALLOW:
        raise ValueError("kdf não suportado")
    # Valida os PARÂMETROS antes de qualquer trabalho pesado (anti-DoS).
    try:
        n, r, p = int(envelope["n"]), int(envelope["r"]), int(envelope["p"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("parâmetros de KDF ausentes/ilegíveis") from exc
    if not (2 <= n <= _N_MAX and (n & (n - 1)) == 0 and 1 <= r <= _R_MAX and 1 <= p <= _P_MAX):
        raise ValueError("parâmetros de KDF fora dos limites")
    salt = _b64d(envelope.get("salt"))
    nonce = _b64d(envelope.get("nonce"))
    if len(salt) != _SALT_LEN or len(nonce) != _NONCE_LEN:
        raise ValueError("salt/nonce com tamanho inválido")
    ct = _b64d(envelope.get("ciphertext"))
    if len(ct) > _CIPHERTEXT_MAX:
        raise ValueError("ciphertext grande demais")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _key(passphrase, salt, n, r, p)
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
